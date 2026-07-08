"""Tests for structured code-defect telemetry (self-repair loop Phase 1)."""

from __future__ import annotations

import json
import logging

import pytest

from agent.defect_capture import (
    DefectRecord,
    append_defect_record,
    build_fingerprint,
    load_defect_summaries,
    sanitize_defect_message,
)


def _make_record(**overrides) -> DefectRecord:
    base = dict(
        defect_id="defect-abcdefabcdefabcd",
        fingerprint="fp-abcdefabcdefabcd",
        timestamp="2026-07-06T12:00:00Z",
        repo="sinria",
        defect_kind="unhandled_exception",
        exc_class="ValueError",
        redacted_message="invalid literal for int",
        code_location="agent/tool_executor.py:247",
        logger_name="agent.tool_executor",
        session_kind="gateway",
        severity="medium",
        transient_likely=False,
    )
    base.update(overrides)
    return DefectRecord(**base)


def test_defect_record_rejects_sensitive_message():
    with pytest.raises(ValueError):
        _make_record(redacted_message="api_key=sk-live-1234567890abcdef")


def test_defect_record_rejects_unsafe_identifier():
    with pytest.raises(ValueError):
        _make_record(defect_id="defect with spaces and / slashes")


def test_fingerprint_is_stable_and_digit_free():
    fp1 = build_fingerprint("sinria", "agent/tool_executor.py", "_invoke_tool", "ValueError")
    fp2 = build_fingerprint("sinria", "agent/tool_executor.py", "_invoke_tool", "ValueError")
    fp3 = build_fingerprint("sinria", "agent/tool_executor.py", "_invoke_tool", "KeyError")
    assert fp1 == fp2
    assert fp1 != fp3
    # Digit-free digests: the shared safety guard treats long numeric runs as
    # potential identifiers/phone numbers (same convention as outcome_gap).
    assert not any(ch.isdigit() for ch in fp1)


def test_sanitize_defect_message_redacts_and_truncates():
    cleaned = sanitize_defect_message("boom sk-live-" + "a" * 40 + " end " + "x" * 500)
    assert "sk-live-" + "a" * 40 not in cleaned
    assert len(cleaned) <= 300


def test_sanitize_defect_message_fail_closed():
    # When redaction cannot make the message clean, drop it entirely.
    assert sanitize_defect_message("patient id: 12345 leaked") == ""
    assert sanitize_defect_message(None) == ""


def test_append_and_aggregate(tmp_path):
    target = tmp_path / "code_defects.jsonl"
    append_defect_record(_make_record(timestamp="2026-07-06T10:00:00Z"), path=target)
    append_defect_record(
        _make_record(defect_id="defect-bcdefabcdefabcda", timestamp="2026-07-06T11:00:00Z"),
        path=target,
    )
    append_defect_record(
        _make_record(
            defect_id="defect-cdefabcdefabcdab",
            fingerprint="fp-bbbbbbbbbbbbbbbb",
            exc_class="KeyError",
            timestamp="2026-07-06T12:00:00Z",
        ),
        path=target,
    )
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["fingerprint"] == "fp-abcdefabcdefabcd"

    summaries = load_defect_summaries(path=target)
    assert len(summaries) == 2
    top = {s.fingerprint: s for s in summaries}
    assert top["fp-abcdefabcdefabcd"].occurrence_count == 2
    assert top["fp-abcdefabcdefabcd"].first_seen == "2026-07-06T10:00:00Z"
    assert top["fp-abcdefabcdefabcd"].last_seen == "2026-07-06T11:00:00Z"
    assert top["fp-bbbbbbbbbbbbbbbb"].occurrence_count == 1
    # Most-recurring first.
    assert summaries[0].fingerprint == "fp-abcdefabcdefabcd"


def test_load_defect_summaries_missing_file(tmp_path):
    assert load_defect_summaries(path=tmp_path / "nope.jsonl") == []


# ── record_exception_defect ──────────────────────────────────────────────


def _raise_value_error():
    raise ValueError("boom with secret api_key=sk-live-1234567890abcdef")


def _capture_exc():
    try:
        _raise_value_error()
    except ValueError:
        import sys

        return sys.exc_info()


def test_record_exception_defect_writes_sanitized_record(tmp_path):
    from agent.defect_capture import record_exception_defect

    target = tmp_path / "code_defects.jsonl"
    exc_type, exc_value, tb = _capture_exc()
    record = record_exception_defect(
        exc_type,
        exc_value,
        tb,
        logger_name="agent.tool_executor",
        session_kind="gateway",
        path=target,
    )
    assert record is not None
    assert record.exc_class == "ValueError"
    assert record.repo == "sinria"
    # The innermost in-repo frame is this test file.
    assert "test_defect_capture.py" in record.code_location
    assert "sk-live-1234567890abcdef" not in record.redacted_message
    data = json.loads(target.read_text(encoding="utf-8").strip())
    assert data["fingerprint"] == record.fingerprint
    assert data["session_kind"] == "gateway"


def test_record_exception_defect_transient_flag(tmp_path):
    from agent.defect_capture import record_exception_defect

    try:
        raise TimeoutError("read timed out")
    except TimeoutError:
        import sys

        exc_type, exc_value, tb = sys.exc_info()
    record = record_exception_defect(exc_type, exc_value, tb, path=tmp_path / "d.jsonl")
    assert record.transient_likely is True
    assert record.severity == "medium"


def test_record_exception_defect_critical_is_high(tmp_path):
    from agent.defect_capture import record_exception_defect

    exc_type, exc_value, tb = _capture_exc()
    record = record_exception_defect(
        exc_type, exc_value, tb, levelno=logging.CRITICAL, path=tmp_path / "d.jsonl",
    )
    assert record.severity == "high"


def test_record_exception_defect_none_tb_returns_none(tmp_path):
    from agent.defect_capture import record_exception_defect

    assert record_exception_defect(ValueError, ValueError("x"), None, path=tmp_path / "d.jsonl") is None


def test_record_exception_defect_pseudo_filename_is_external(tmp_path):
    from agent.defect_capture import record_exception_defect

    # Frames from exec'd strings (<string>, <stdin>) are not repo files and
    # must not produce in-repo fix locations.
    try:
        exec(compile("raise ValueError('from exec')", "<string>", "exec"))
    except ValueError:
        import sys

        exc_type, exc_value, tb = sys.exc_info()
    # Drop this test file's own frames to simulate a pure pseudo-file traceback.
    while tb is not None and tb.tb_frame.f_code.co_filename != "<string>":
        tb = tb.tb_next
    record = record_exception_defect(exc_type, exc_value, tb, path=tmp_path / "d.jsonl")
    assert record.repo == "external"
    assert record.code_location == "unknown:0"


# ── DefectCaptureHandler + config flag + logging wiring ─────────────────


def _fresh_logger(name, handler):
    log = logging.getLogger(name)
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    return log


def test_handler_captures_error_with_exc_info(tmp_path):
    from agent.defect_capture import DefectCaptureHandler

    target = tmp_path / "code_defects.jsonl"
    log = _fresh_logger("test.defect.capture1", DefectCaptureHandler(path=target))
    try:
        raise KeyError("missing")
    except KeyError:
        log.error("boom: %s", "missing", exc_info=True)
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["exc_class"] == "KeyError"


def test_handler_ignores_records_without_exc_info(tmp_path):
    from agent.defect_capture import DefectCaptureHandler

    target = tmp_path / "code_defects.jsonl"
    log = _fresh_logger("test.defect.capture2", DefectCaptureHandler(path=target))
    log.error("plain error, no traceback")
    log.warning("warning below level")
    assert not target.exists()


def test_handler_never_raises(tmp_path, monkeypatch):
    import agent.defect_capture as dc

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(dc, "record_exception_defect", _boom)
    log = _fresh_logger("test.defect.capture3", dc.DefectCaptureHandler(path=tmp_path / "d.jsonl"))
    try:
        raise ValueError("x")
    except ValueError:
        # A leaked exception here fails the test itself — the contract is
        # that telemetry can never break logging.
        log.error("boom", exc_info=True)


def test_repair_telemetry_enabled_flag():
    from agent.defect_capture import repair_telemetry_enabled

    assert repair_telemetry_enabled({}) is True
    assert repair_telemetry_enabled({"repair": {}}) is True
    assert repair_telemetry_enabled({"repair": {"telemetry": True}}) is True
    assert repair_telemetry_enabled({"repair": {"telemetry": False}}) is False
    assert repair_telemetry_enabled({"repair": {"telemetry": "off"}}) is False
    assert repair_telemetry_enabled({"repair": "not-a-dict"}) is True


def test_setup_logging_attaches_handler_once(tmp_path, monkeypatch):
    import hermes_logging
    from agent.defect_capture import DefectCaptureHandler

    monkeypatch.setattr(hermes_logging, "_logging_initialized", False)
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, DefectCaptureHandler)]:
        root.removeHandler(existing)
    try:
        hermes_logging.setup_logging(hermes_home=tmp_path, force=True)
        hermes_logging.setup_logging(hermes_home=tmp_path, force=True)
        attached = [h for h in root.handlers if isinstance(h, DefectCaptureHandler)]
        assert len(attached) == 1
    finally:
        for existing in [h for h in root.handlers if isinstance(h, DefectCaptureHandler)]:
            root.removeHandler(existing)
