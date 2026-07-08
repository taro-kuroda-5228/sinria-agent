from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

import pytest


SECRET_FIXTURE = "sk-" + "liv...cdef"
SECRET_EVENT_FIXTURE = "sk-" + "liv...6789"

DEMO = f"""
Open the internal billing dashboard.
Paste API key {SECRET_FIXTURE} into a form.
For customer taro@example.com, choose the June date range.
Click Download CSV and verify the file appears in Downloads.
"""


def test_build_capture_session_from_event_log_sanitizes_and_creates_checkpoints():
    from hermes_cli.skill_recorder import build_capture_session

    session = build_capture_session(
        [
            {"tool": "browser", "action": "Open dashboard for taro@example.com"},
            {"tool": "browser", "action": f"Paste token {SECRET_EVENT_FIXTURE} into field"},
            {"tool": "browser", "action": "Click Download CSV"},
        ],
        source="browser-tool-history",
    )

    serialized = __import__("json").dumps(session, ensure_ascii=False)
    assert session["source"] == "browser-tool-history"
    assert session["raw_recording_stored"] is False
    assert session["review_required"] is True
    assert session["external_action_performed"] is False
    assert session["steps"][0]["tool"] == "browser"
    assert session["checkpoints"][0]["status"] == "pending_verification"
    assert "taro@example.com" not in serialized
    assert SECRET_EVENT_FIXTURE not in serialized
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_build_recorded_skill_sanitizes_demo_and_uses_sinria_wording():
    from hermes_cli.skill_recorder import build_recorded_skill

    content = build_recorded_skill(
        name="billing-report-download",
        description="Download a recurring billing report from the internal dashboard.",
        transcript=DEMO,
        trigger="When a billing CSV report must be downloaded.",
        required_inputs=["date range", "output folder"],
    )

    assert content.startswith("---\n")
    assert "name: billing-report-download" in content
    assert "description: Download a recurring billing report" in content
    assert "Sinria" in content
    assert "## When to Use" in content
    assert "## Required Inputs" in content
    assert "## Procedure" in content
    assert "## Replay Checkpoints" in content
    assert "## Human Review" in content
    assert "## Verification" in content
    assert "date range" in content
    assert "output folder" in content
    assert SECRET_FIXTURE not in content
    assert "taro@example.com" not in content
    assert "[REDACTED_SECRET]" in content
    assert "[REDACTED_EMAIL]" in content
    assert "Hermes" not in content


def test_write_recorded_skill_creates_inspectable_skill_without_raw_demo_by_default(tmp_path):
    from hermes_cli.skill_recorder import write_recorded_skill

    result = write_recorded_skill(
        skills_dir=tmp_path / "skills",
        name="billing-report-download",
        category="operations",
        description="Download a recurring billing report from the internal dashboard.",
        transcript=DEMO,
        trigger="When a billing CSV report must be downloaded.",
        required_inputs=["date range"],
    )

    assert result["success"] is True
    skill_md = Path(result["skill_md"])
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert "## Procedure" in content
    assert "## Replay Checkpoints" in content
    assert "## Human Review" in content
    assert SECRET_FIXTURE not in content
    review_bundle = skill_md.parent / "references" / "review-bundle.json"
    checklist = skill_md.parent / "references" / "replay-checklist.md"
    assert review_bundle.exists()
    assert checklist.exists()
    review_text = review_bundle.read_text(encoding="utf-8")
    assert "taro@example.com" not in review_text
    assert SECRET_FIXTURE not in review_text
    assert '"review_required": true' in review_text
    assert '"external_action_performed": false' in review_text
    assert not (skill_md.parent / "references" / "recording.md").exists()


def test_write_recorded_skill_can_save_sanitized_recording_reference(tmp_path):
    from hermes_cli.skill_recorder import write_recorded_skill

    result = write_recorded_skill(
        skills_dir=tmp_path / "skills",
        name="billing-report-download",
        description="Download a recurring billing report from the internal dashboard.",
        transcript=DEMO,
        save_demo=True,
    )

    recording = Path(result["skill_md"]).parent / "references" / "recording.md"
    assert recording.exists()
    text = recording.read_text(encoding="utf-8")
    assert "Sanitized recording notes" in text
    assert SECRET_FIXTURE not in text
    assert "taro@example.com" not in text
    assert "[REDACTED_SECRET]" in text


def test_write_recorded_skill_rejects_existing_skill_without_force(tmp_path):
    from hermes_cli.skill_recorder import write_recorded_skill

    kwargs = dict(
        skills_dir=tmp_path / "skills",
        name="billing-report-download",
        description="Download a recurring billing report from the internal dashboard.",
        transcript=DEMO,
    )
    assert write_recorded_skill(**kwargs)["success"] is True

    second = write_recorded_skill(**kwargs)

    assert second["success"] is False
    assert "already exists" in second["error"]




def test_read_recording_input_supports_stdin(monkeypatch):
    from hermes_cli.skill_recorder import read_recording_input

    monkeypatch.setattr("sys.stdin.read", lambda: "step one\nstep two")

    assert read_recording_input("-") == "step one\nstep two"


def test_skills_record_command_creates_skill_from_local_notes(tmp_path):
    from hermes_cli.skills_hub import do_record

    notes = tmp_path / "recording.md"
    notes.write_text(DEMO, encoding="utf-8")
    sink = Console(file=(out := __import__("io").StringIO()), force_terminal=False, color_system=None)

    result = do_record(
        input_path=str(notes),
        name="billing-report-download",
        description="Download a recurring billing report from the internal dashboard.",
        category="operations",
        trigger="When a billing CSV report must be downloaded.",
        required_inputs=["date range"],
        skills_dir=tmp_path / "skills",
        console=sink,
    )

    assert result["success"] is True
    skill_md = Path(result["skill_md"])
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert "Sinria" in content
    assert SECRET_FIXTURE not in content
    assert "taro@example.com" not in content
    assert not (skill_md.parent / "references" / "recording.md").exists()
    printed = out.getvalue()
    assert "Created recorded workflow skill" in printed
    assert "raw recording was not stored" in printed
    assert SECRET_FIXTURE not in printed


def test_skills_command_routes_record_action(tmp_path):
    from hermes_cli.skills_hub import skills_command

    notes = tmp_path / "recording.md"
    notes.write_text(DEMO, encoding="utf-8")
    args = SimpleNamespace(
        skills_action="record",
        input=str(notes),
        name="billing-report-download",
        description="Download a recurring billing report from the internal dashboard.",
        category="operations",
        trigger="When a billing CSV report must be downloaded.",
        required_input=["date range"],
        skills_dir=str(tmp_path / "skills"),
        save_demo=False,
        force=False,
    )

    skills_command(args)

    assert (tmp_path / "skills" / "operations" / "billing-report-download" / "SKILL.md").exists()


def test_build_video_capture_session_keeps_video_local_and_sanitizes_notes(tmp_path):
    from hermes_cli.skill_recorder import build_video_capture_session

    video = tmp_path / "taro@example.com-demo.mp4"
    video.write_bytes(b"fake video bytes")

    session = build_video_capture_session(
        video,
        notes=f"Open admin screen for taro@example.com and paste {SECRET_FIXTURE}",
    )

    serialized = __import__("json").dumps(session, ensure_ascii=False)
    assert session["schema_version"] == "sinria.video_recorded_workflow.v1"
    assert session["raw_video_stored_in_skill"] is False
    assert session["raw_recording_stored"] is False
    assert session["video_artifact"]["local_path_stored"] is False
    assert session["video_artifact"]["filename"] == "[REDACTED_EMAIL]-demo.mp4"
    assert session["video_artifact"]["sha256_12"]
    assert "taro@example.com" not in serialized
    assert SECRET_FIXTURE not in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_write_recorded_video_skill_creates_skill_and_video_capture_metadata_without_copying_video(tmp_path):
    from hermes_cli.skill_recorder import write_recorded_video_skill

    video = tmp_path / "operator-demo.mp4"
    video.write_bytes(b"fake video bytes")

    result = write_recorded_video_skill(
        skills_dir=tmp_path / "skills",
        name="screen-demo-replay",
        category="operations",
        description="Replay a screen-recorded workflow safely.",
        video_path=video,
        notes=f"Click export for taro@example.com after entering {SECRET_FIXTURE}",
        required_inputs=["current account"],
    )

    assert result["success"] is True
    skill_md = Path(result["skill_md"])
    content = skill_md.read_text(encoding="utf-8")
    assert "Generated from a local screen/video recording" in content
    assert "raw video is not stored" in content
    assert "## Replay Checkpoints" in content
    assert "taro@example.com" not in content
    assert SECRET_FIXTURE not in content
    capture_json = skill_md.parent / "references" / "video-capture.json"
    assert capture_json.exists()
    capture_text = capture_json.read_text(encoding="utf-8")
    assert '"raw_video_stored_in_skill": false' in capture_text
    assert '"local_path_stored": false' in capture_text
    assert str(video) not in capture_text
    assert not (skill_md.parent / video.name).exists()


def test_skills_record_video_command_creates_skill_from_existing_local_video(tmp_path):
    from hermes_cli.skills_hub import do_record_video

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    sink = Console(file=(out := __import__("io").StringIO()), force_terminal=False, color_system=None)

    result = do_record_video(
        video_path=str(video),
        name="screen-demo-replay",
        description="Replay a screen-recorded workflow safely.",
        category="operations",
        notes="Open the dashboard, click export, verify the downloaded file.",
        required_inputs=["current account"],
        skills_dir=tmp_path / "skills",
        console=sink,
    )

    assert result["success"] is True
    assert (tmp_path / "skills" / "operations" / "screen-demo-replay" / "SKILL.md").exists()
    printed = out.getvalue()
    assert "Created video-recorded workflow skill" in printed
    assert "raw video was not copied into the skill" in printed
    assert "Hermes" not in printed


def test_record_video_interactive_metadata_builds_notes_and_next_steps(tmp_path, monkeypatch):
    from hermes_cli.skills_hub import do_record_video

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    answers = iter(
        [
            "screen-demo-replay",
            "Replay a screen-recorded workflow safely.",
            "Review approval queue",
            "No external send; no raw customer body",
            "Waiting items counted and no send performed",
            "current workspace",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    sink = Console(file=(out := __import__("io").StringIO()), force_terminal=False, color_system=None)

    result = do_record_video(
        str(video),
        name=None,
        description=None,
        interactive=True,
        skills_dir=tmp_path / "skills",
        console=sink,
    )

    assert result["success"] is True
    skill_md = Path(result["skill_md"])
    content = skill_md.read_text(encoding="utf-8")
    assert "Purpose: Review approval queue" in content
    assert "Must not: No external send; no raw customer body" in content
    assert "Done when: Waiting items counted and no send performed" in content
    assert "current workspace" in content
    printed = out.getvalue()
    assert "Next steps" in printed
    assert "Company OS Review" in printed
    assert "sinria chat --skills screen-demo-replay" in printed
    assert "Hermes" not in printed


def test_skills_command_routes_record_video_action(tmp_path):
    from hermes_cli.skills_hub import skills_command

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    args = SimpleNamespace(
        skills_action="record-video",
        video=str(video),
        record_duration=None,
        output=None,
        name="screen-demo-replay",
        description="Replay a screen-recorded workflow safely.",
        category="operations",
        notes="Open dashboard and verify export.",
        notes_file=None,
        required_input=["current account"],
        skills_dir=str(tmp_path / "skills"),
        save_demo=False,
        force=False,
    )

    skills_command(args)

    assert (tmp_path / "skills" / "operations" / "screen-demo-replay" / "references" / "video-capture.json").exists()


def test_extract_video_workflow_steps_from_local_frame_manifest_without_storing_frames(tmp_path, monkeypatch):
    from hermes_cli import skill_recorder

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")

    def fake_which(name):
        return f"/usr/bin/{name}" if name in {"ffmpeg", "ffprobe"} else None

    def fake_run(cmd, check=False, capture_output=False, text=False, **_kwargs):
        if "ffprobe" in cmd[0]:
            return SimpleNamespace(returncode=0, stdout="12.0\n", stderr="")
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(f"frame for {out.name}".encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_recorder.shutil, "which", fake_which)
    monkeypatch.setattr(skill_recorder.subprocess, "run", fake_run)

    extracted = skill_recorder.extract_video_workflow_steps(video, notes=f"Click export for taro@example.com with {SECRET_FIXTURE}", max_frames=3)

    serialized = __import__("json").dumps(extracted, ensure_ascii=False)
    assert extracted["schema_version"] == "sinria.video_step_extraction.v1"
    assert extracted["local_only"] is True
    assert extracted["raw_frames_stored"] is False
    assert extracted["external_ai_used"] is False
    assert len(extracted["key_moments"]) == 3
    assert all("frame_sha256_12" in moment for moment in extracted["key_moments"])
    assert "taro@example.com" not in serialized
    assert SECRET_FIXTURE not in serialized
    assert "[REDACTED_SECRET]" in serialized
    assert not any(tmp_path.glob("frame-*.jpg")), "temporary extracted frames must not leak beside the video"


def test_extract_video_workflow_steps_gracefully_degrades_without_ffmpeg(tmp_path, monkeypatch):
    from hermes_cli import skill_recorder

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setattr(skill_recorder.shutil, "which", lambda _name: None)

    extracted = skill_recorder.extract_video_workflow_steps(video, notes="Open dashboard")

    assert extracted["extraction_status"] == "metadata_only_ffmpeg_unavailable"
    assert extracted["raw_frames_stored"] is False
    assert "brew install ffmpeg" in extracted["operator_next_action"]
    assert "--video <existing local recording>" in extracted["operator_next_action"]


def test_write_recorded_video_skill_includes_automatic_step_extraction_metadata(tmp_path, monkeypatch):
    from hermes_cli import skill_recorder

    video = tmp_path / "operator-demo.mp4"
    video.write_bytes(b"fake video bytes")

    monkeypatch.setattr(
        skill_recorder,
        "extract_video_workflow_steps",
        lambda *args, **kwargs: {
            "schema_version": "sinria.video_step_extraction.v1",
            "extraction_status": "local_keyframe_manifest",
            "local_only": True,
            "raw_frames_stored": False,
            "external_ai_used": False,
            "key_moments": [{"index": 1, "timestamp_seconds": 0, "frame_sha256_12": "abc123def456"}],
            "inferred_steps": ["Review local key moment 1 at 00:00:00 and map it to a reusable UI action."],
        },
    )

    result = skill_recorder.write_recorded_video_skill(
        skills_dir=tmp_path / "skills",
        name="screen-demo-replay",
        category="operations",
        description="Replay a screen-recorded workflow safely.",
        video_path=video,
    )

    extraction_path = Path(result["video_step_extraction"])
    assert extraction_path.exists()
    extraction = extraction_path.read_text(encoding="utf-8")
    assert "sinria.video_step_extraction.v1" in extraction
    assert "abc123def456" in extraction
    assert str(video) not in extraction
