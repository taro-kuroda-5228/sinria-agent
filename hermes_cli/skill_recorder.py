"""Local Sinria workflow-to-skill recorder.

This module implements a privacy-first analogue of Codex Record & Replay:
operators can capture a concise workflow demonstration/notes file once and turn
it into an inspectable, editable Sinria skill under ~/.sinria/skills.

It intentionally does not keep raw recordings by default.  If requested, only a
sanitized reference is saved.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from hermes_constants import get_sinria_home

DEFAULT_SKILLS_DIR = get_sinria_home() / "skills"
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VALID_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SECRET_TOKEN_PATTERNS = [
    re.compile(r"\[[Rr][Ee][Dd][Aa][Cc][Tt][Ee][Dd][-_ ][A-Za-z0-9_ -]+\]"),
    re.compile(r"\b(?:sk|rk|pk|ghp|gho|ghu|github_pat|xox[baprs])-[-A-Za-z0-9_.]{4,}\b"),
    re.compile(r"\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+", re.IGNORECASE),
]


def _yaml_scalar(value: str) -> str:
    """Return a compact YAML scalar, quoted only when needed."""
    if value and re.match(r"^[A-Za-z0-9][A-Za-z0-9 .,_()/'-]*$", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _validate_name(name: str) -> str | None:
    if not name or not _VALID_NAME_RE.match(name):
        return (
            "Skill name must be lowercase letters, digits, '.', '_' or '-', "
            "start with a letter/digit, and be at most 64 characters."
        )
    return None


def _validate_category(category: str | None) -> str | None:
    if not category:
        return None
    if "/" in category or "\\" in category or not _VALID_CATEGORY_RE.match(category):
        return "Category must be one safe directory segment (lowercase letters, digits, '.', '_' or '-')."
    return None


def sanitize_recording_text(text: str) -> str:
    """Return demo notes safe enough to store in a skill/reference file."""
    sanitized = text or ""
    for pattern in _SECRET_TOKEN_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
    # Collapse very long opaque identifiers that often carry customer/patient IDs.
    sanitized = re.sub(r"\b[A-Z]{2,}-\d{4,}\b", "[REDACTED_IDENTIFIER]", sanitized)
    return sanitized.strip()


def _normalize_event(event: Any, index: int) -> dict[str, Any]:
    if isinstance(event, dict):
        action = str(event.get("action") or event.get("text") or event.get("content") or event).strip()
        tool = str(event.get("tool") or event.get("source") or "manual").strip() or "manual"
    else:
        action = str(event).strip()
        tool = "manual"
    sanitized_action = sanitize_recording_text(action)
    return {
        "index": index,
        "tool": sanitize_recording_text(tool),
        "action": sanitized_action,
        "checkpoint": f"Confirm step {index} completed before continuing.",
    }


def build_capture_session(events: Iterable[Any], *, source: str = "manual-notes") -> dict[str, Any]:
    """Build a sanitized structured capture session from notes/tool-history events."""
    steps = [_normalize_event(event, idx) for idx, event in enumerate(events, start=1)]
    steps = [step for step in steps if step["action"]]
    if not steps:
        steps = [_normalize_event("Capture or describe the workflow steps, then refine before replay.", 1)]
    checkpoints = [
        {
            "index": step["index"],
            "label": step["checkpoint"],
            "status": "pending_verification",
        }
        for step in steps
    ]
    return {
        "schema_version": "sinria.recorded_workflow.v1",
        "source": sanitize_recording_text(source or "manual-notes"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_recording_stored": False,
        "sanitized_only": True,
        "review_required": True,
        "external_action_performed": False,
        "steps": steps,
        "checkpoints": checkpoints,
    }


def _sha256_12(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _format_seconds(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ffmpeg_install_hint() -> str:
    return (
        "Install ffmpeg locally (macOS: `brew install ffmpeg`; Ubuntu/Debian: "
        "`sudo apt-get install ffmpeg`) or pass --video <existing local recording>."
    )


def _probe_video_duration(video: Path, ffprobe: str | None) -> float | None:
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return float((proc.stdout or "").strip())
    except Exception:
        return None
    return None


def _sample_timestamps(duration: float | None, max_frames: int) -> list[float]:
    count = max(1, min(max_frames, 12))
    if not duration or duration <= 0:
        return [float(i * 5) for i in range(count)]
    if count == 1:
        return [0.0]
    usable_end = max(0.0, duration - 0.2)
    return [round((usable_end * idx) / (count - 1), 2) for idx in range(count)]


def extract_video_workflow_steps(video_path: Path | str, *, notes: str = "", max_frames: int = 6) -> dict[str, Any]:
    """Extract a local-only, sanitized key-moment manifest from a video.

    This intentionally avoids cloud OCR/vision and does not persist extracted
    frames. The result gives Sinria and a human reviewer stable timestamps and
    frame hashes to turn a recording into replayable steps without copying raw
    screen contents into the skill.
    """
    video = Path(video_path).expanduser()
    if not video.exists():
        raise FileNotFoundError(f"Video recording not found: {video}")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    base: dict[str, Any] = {
        "schema_version": "sinria.video_step_extraction.v1",
        "source": "local-screen-video-recording",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "local_only": True,
        "external_ai_used": False,
        "raw_frames_stored": False,
        "raw_video_stored": False,
        "sanitized_only": True,
        "review_required": True,
        "video_artifact": {
            "filename": sanitize_recording_text(video.name),
            "suffix": sanitize_recording_text(video.suffix.lower()),
            "sha256_12": _sha256_12(video),
            "local_path_stored": False,
            "copied_into_skill": False,
        },
        "sanitized_notes": sanitize_recording_text(notes),
    }
    if not ffmpeg:
        return {
            **base,
            "extraction_status": "metadata_only_ffmpeg_unavailable",
            "key_moments": [],
            "inferred_steps": _video_events_from_notes(notes),
            "operator_next_action": _ffmpeg_install_hint(),
        }

    duration = _probe_video_duration(video, ffprobe)
    moments: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sinria-video-frames-") as tmp:
        tmpdir = Path(tmp)
        for index, ts in enumerate(_sample_timestamps(duration, max_frames), start=1):
            frame = tmpdir / f"frame-{index:02d}.jpg"
            proc = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-ss", str(ts), "-i", str(video), "-frames:v", "1", str(frame)],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not frame.exists():
                continue
            moments.append(
                {
                    "index": index,
                    "timestamp_seconds": ts,
                    "timestamp_label": _format_seconds(ts),
                    "frame_sha256_12": _sha256_12(frame),
                    "frame_stored": False,
                    "review_prompt": "Map this local-only key moment to a reusable UI/action step; do not copy private screen text.",
                }
            )

    inferred = [
        f"Review local key moment {m['index']} at {m['timestamp_label']} and map it to a reusable UI action."
        for m in moments
    ]
    if notes:
        inferred.extend(_line_items(notes))
    if not inferred:
        inferred = _video_events_from_notes(notes)
    return {
        **base,
        "extraction_status": "local_keyframe_manifest" if moments else "metadata_only_no_frames_extracted",
        "duration_seconds": duration,
        "key_moments": moments,
        "inferred_steps": [sanitize_recording_text(step) for step in inferred],
        "operator_next_action": "Review video-step-extraction.json locally, refine SKILL.md, then verify the real workflow before replay.",
    }


def _video_events_from_notes(notes: str) -> list[str]:
    items = _line_items(notes)
    if items == ["Capture or describe the workflow steps, then refine this skill before replay."]:
        return [
            "Review the local screen recording artifact listed in references/video-capture.json.",
            "Identify the operator intent and reusable UI/action sequence from the recording before replay.",
            "Replay only with current task inputs, keeping external sends and production writes approval-gated.",
        ]
    return [
        "Review the local screen recording artifact listed in references/video-capture.json.",
        *items,
        "Verify the final workflow result in the real UI or file/output location before reporting done.",
    ]


def build_video_capture_session(video_path: Path | str, *, notes: str = "") -> dict[str, Any]:
    """Build sanitized metadata for a local video/screen recording capture.

    The raw video path and video bytes are intentionally not copied into durable
    skill artifacts.  The metadata is enough for local human review and replay
    while keeping private screen contents local-only.
    """
    path = Path(video_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Video recording not found: {path}")
    events = _video_events_from_notes(notes)
    capture = build_capture_session(events, source="local-screen-video-recording")
    return {
        "schema_version": "sinria.video_recorded_workflow.v1",
        "source": "local-screen-video-recording",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_video_stored_in_skill": False,
        "raw_recording_stored": False,
        "local_path_stored": False,
        "copied_into_skill": False,
        "sanitized_only": True,
        "review_required": True,
        "external_action_performed": False,
        "video_artifact": {
            "filename": sanitize_recording_text(path.name),
            "suffix": sanitize_recording_text(path.suffix.lower()),
            "size_bytes": path.stat().st_size,
            "sha256_12": _sha256_12(path),
            "local_path_stored": False,
            "copied_into_skill": False,
        },
        "capture_session": capture,
        "review_checklist": [
            "Review the raw video only on the local machine; do not upload it to a cloud editor without explicit approval.",
            "Convert only reusable, sanitized operator steps into SKILL.md instructions.",
            "Confirm the generated skill contains no raw credentials, PHI/PII, private screen contents, or patient/customer identifiers.",
            "Keep external sends, production writes, deletes, billing/auth changes, and clinical/patient-data actions approval-gated.",
        ],
    }


def build_video_recording_transcript(video_path: Path | str, *, notes: str = "") -> str:
    """Return sanitized notes that can become the SKILL.md procedure for a video capture."""
    path = Path(video_path).expanduser()
    events = _video_events_from_notes(notes)
    basename = sanitize_recording_text(path.name)
    return "\n".join(
        [
            "Generated from a local screen/video recording.",
            f"Recording artifact: {basename}; raw video is not stored in the skill.",
            "Use references/video-capture.json to locate the local review metadata without exposing the raw path.",
            *events,
        ]
    )


def record_local_screen_video(output_path: Path | str, *, duration: int = 30) -> Path:
    """Record a short local screen video with ffmpeg, returning the output path.

    This is a local-only helper.  It never uploads frames.  On macOS it uses the
    avfoundation screen input.  Other platforms fail closed with an actionable
    error until a local capture backend is explicitly configured.
    """
    if duration <= 0:
        raise ValueError("record duration must be positive")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(f"ffmpeg is required for `sinria skills record-video --record-duration`. {_ffmpeg_install_hint()}")
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    system = platform.system().lower()
    if system == "darwin":
        cmd = [ffmpeg, "-y", "-f", "avfoundation", "-framerate", "15", "-i", "1:none", "-t", str(duration), str(output)]
    else:
        raise RuntimeError("Automatic screen video capture is currently implemented for macOS only; pass --video <existing local recording> on this platform.")
    subprocess.run(cmd, check=True)
    return output


def _line_items(text: str) -> list[str]:
    lines: list[str] = []
    for raw in sanitize_recording_text(text).splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        if line:
            lines.append(line)
    if not lines:
        return ["Capture or describe the workflow steps, then refine this skill before replay."]
    return lines


def _format_bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _format_numbered(items: Sequence[str]) -> str:
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))


def _format_checkpoints(items: Sequence[str]) -> str:
    return "\n".join(
        f"- [ ] Step {idx}: verify `{item[:96]}` completed before continuing."
        for idx, item in enumerate(items, start=1)
    )


def _normalize_inputs(required_inputs: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for item in required_inputs or []:
        value = str(item).strip()
        if value:
            out.append(value)
    return out or ["Task-specific values that differ from the recorded demonstration."]


def build_recorded_skill(
    *,
    name: str,
    description: str,
    transcript: str,
    trigger: str | None = None,
    required_inputs: Iterable[str] | None = None,
) -> str:
    """Build a Sinria SKILL.md from sanitized workflow recording notes."""
    name = name.strip().lower()
    description = (description or "Reusable workflow recorded for Sinria replay.").strip()
    trigger_text = (trigger or f"Use when repeating the recorded workflow for `{name}`.").strip()
    inputs = _normalize_inputs(required_inputs)
    steps = _line_items(transcript)

    return f"""---
name: {name}
description: {_yaml_scalar(description)}
version: 1.0.0
author: Sinria workflow recorder
license: MIT
metadata:
  sinria:
    generated_by: skill-recorder
    source: local-sanitized-recording
    raw_recording_stored: false
---

# {name}

## Overview

This skill was generated from a local Sinria workflow recording/notes file.
It is meant to make a recurring workflow replayable while keeping raw
confidential context, credentials, PHI/PII, and private screen contents out
of durable shared instructions by default.

## When to Use

{trigger_text}

## Required Inputs

{_format_bullets(inputs)}

## Procedure

{_format_numbered(steps)}

## Replay Checkpoints

{_format_checkpoints(steps)}

## Human Review

- Review this generated skill before sharing it with a team or running it against production/confidential systems.
- Confirm each procedure step is generic enough to replay without embedding raw credentials, PHI/PII, patient/customer identifiers, or private screen contents.
- Keep high-risk steps approval-gated: external sends, production writes, deletes, billing/auth changes, and clinical/patient-data actions.

## Verification

- Confirm the final artifact/state requested by the workflow exists.
- Check that any date range, file path, account, case, or record selector matches the current task inputs.
- Do not report completion until the real workflow result is visible or otherwise verified.
- If replay is blocked, report the stop point, sanitized cause, risk, and the next user decision needed.

## Safety Gates

- Do not store or repeat raw credentials, PHI/PII, patient identifiers, customer-private bodies, or classified/confidential context in the skill.
- External sends, production writes, deletes, billing/auth changes, and clinical/patient-data actions require explicit human approval before execution.
- Prefer local/on-prem processing; external egress should use sanitized metadata only unless approved.

## Replay Notes

Start a new Sinria session with this skill loaded, then provide the current
task-specific inputs. Example:

```bash
sinria chat --skills {name} -q "Replay this workflow for <current inputs>. Verify the result before reporting done."
```
"""


def read_recording_input(input_path: str) -> str:
    """Read recording notes from a file path, or '-' for stdin."""
    if input_path == "-":
        return sys.stdin.read()
    return Path(input_path).expanduser().read_text(encoding="utf-8")


def _skill_dir(skills_dir: Path, category: str | None, name: str) -> Path:
    return skills_dir / category / name if category else skills_dir / name


def _build_review_bundle(
    *,
    name: str,
    description: str,
    category: str | None,
    transcript: str,
    trigger: str | None,
    required_inputs: Iterable[str] | None,
) -> dict[str, Any]:
    capture = build_capture_session(_line_items(transcript), source="local-sanitized-recording")
    return {
        "schema_version": "sinria.recorded_skill_review.v1",
        "skill_name": name,
        "category": category or "",
        "description": sanitize_recording_text(description),
        "trigger": sanitize_recording_text(trigger or ""),
        "required_inputs": [sanitize_recording_text(str(item)) for item in required_inputs or []],
        "review_required": True,
        "raw_recording_stored": False,
        "external_action_performed": False,
        "sanitized_only": True,
        "capture_session": capture,
        "review_checklist": [
            "Skill instructions contain no raw credentials, PHI/PII, patient/customer identifiers, or private screen contents.",
            "Replay checkpoints are specific enough to verify the workflow result before reporting completion.",
            "External sends, production writes, deletes, billing/auth changes, and clinical/patient-data actions remain approval-gated.",
        ],
    }


def _write_replay_checklist(target: Path, steps: Sequence[str]) -> Path:
    checklist = target / "references" / "replay-checklist.md"
    checklist.parent.mkdir(parents=True, exist_ok=True)
    checklist.write_text(
        "# Replay checklist\n\n"
        "Use this local checklist while replaying the recorded Sinria workflow.\n\n"
        + _format_checkpoints(steps)
        + "\n\n## Completion gate\n\n"
        "- [ ] The real workflow result is visible or otherwise verified.\n"
        "- [ ] No external/prod/clinical side effect was performed without explicit approval.\n",
        encoding="utf-8",
    )
    return checklist


def _write_review_bundle(target: Path, bundle: dict[str, Any]) -> Path:
    review = target / "references" / "review-bundle.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return review


def write_recorded_skill(
    *,
    skills_dir: Path | str = DEFAULT_SKILLS_DIR,
    name: str,
    description: str,
    transcript: str,
    category: str | None = None,
    trigger: str | None = None,
    required_inputs: Iterable[str] | None = None,
    save_demo: bool = False,
    force: bool = False,
) -> dict:
    """Create/update a local skill from recording notes."""
    name = name.strip().lower()
    category = (category or "").strip() or None
    err = _validate_name(name) or _validate_category(category)
    if err:
        return {"success": False, "error": err}

    skills_root = Path(skills_dir).expanduser()
    target = _skill_dir(skills_root, category, name)
    if target.exists():
        if not force:
            return {"success": False, "error": f"Skill '{name}' already exists at {target}. Use --force to replace it."}
        shutil.rmtree(target)

    content = build_recorded_skill(
        name=name,
        description=description,
        transcript=transcript,
        trigger=trigger,
        required_inputs=required_inputs,
    )
    target.mkdir(parents=True, exist_ok=True)
    skill_md = target / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")

    steps = _line_items(transcript)
    replay_checklist = _write_replay_checklist(target, steps)
    review_bundle_path = _write_review_bundle(
        target,
        _build_review_bundle(
            name=name,
            description=description,
            category=category,
            transcript=transcript,
            trigger=trigger,
            required_inputs=required_inputs,
        ),
    )

    if save_demo:
        recording = target / "references" / "recording.md"
        recording.parent.mkdir(parents=True, exist_ok=True)
        recording.write_text(
            "# Sanitized recording notes\n\n"
            "Raw recording content is not stored. This reference contains only sanitized notes.\n\n"
            + sanitize_recording_text(transcript)
            + "\n",
            encoding="utf-8",
        )

    result = {
        "success": True,
        "message": f"Recorded workflow skill '{name}' created.",
        "path": str(target),
        "skill_md": str(skill_md),
        "replay_checklist": str(replay_checklist),
        "review_bundle": str(review_bundle_path),
        "raw_recording_stored": False,
    }
    if category:
        result["category"] = category
    if save_demo:
        result["sanitized_recording"] = str(target / "references" / "recording.md")
    return result


def write_recorded_video_skill(
    *,
    skills_dir: Path | str = DEFAULT_SKILLS_DIR,
    name: str,
    description: str,
    video_path: Path | str,
    notes: str = "",
    category: str | None = None,
    trigger: str | None = None,
    required_inputs: Iterable[str] | None = None,
    save_demo: bool = False,
    force: bool = False,
) -> dict:
    """Create a local skill from a local video/screen recording.

    The video is analyzed only as a local artifact. It is never copied into the
    skill directory; generated artifacts contain sanitized metadata and replay
    instructions only.
    """
    video = Path(video_path).expanduser()
    transcript = build_video_recording_transcript(video, notes=notes)
    result = write_recorded_skill(
        skills_dir=skills_dir,
        name=name,
        description=description,
        transcript=transcript,
        category=category,
        trigger=trigger or f"Use when replaying the screen-recorded workflow for `{name}`.",
        required_inputs=required_inputs,
        save_demo=save_demo,
        force=force,
    )
    if not result.get("success"):
        return result

    target = Path(result["path"])
    capture_path = target / "references" / "video-capture.json"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_text(
        json.dumps(build_video_capture_session(video, notes=notes), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    extraction_path = target / "references" / "video-step-extraction.json"
    extraction_path.write_text(
        json.dumps(extract_video_workflow_steps(video, notes=notes), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["video_capture"] = str(capture_path)
    result["video_step_extraction"] = str(extraction_path)
    result["raw_video_stored_in_skill"] = False
    return result
