#!/usr/bin/env python3
"""video-studio: suggest cut points from a media file (Opus-Clip-style).

Two modes:
  silence  -- ffmpeg silencedetect; returns silent spans + the complementary
              "keep" segments (the spoken parts you'd assemble a tight cut from).
  scene    -- ffmpeg scene-change detection; returns timestamps of hard cuts.

Output is JSON to stdout (and optionally a file). It only *suggests* — the
human/agent decides which segments to add to the timeline via studio.py.

Dependencies: python3 stdlib + ffmpeg/ffprobe on PATH.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _die(msg: str, code: int = 1):
    print(f"[autocut] error: {msg}", file=sys.stderr)
    sys.exit(code)


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def detect_silence(path: str, noise: str, min_silence: float) -> list[dict]:
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-af",
         f"silencedetect=noise={noise}:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True)
    log = proc.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        spans.append({"start": round(s, 3), "end": round(e, 3) if e is not None else None})
    return spans


def keep_segments(duration: float, silences: list[dict]) -> list[dict]:
    """Complement of the silence spans = the parts worth keeping."""
    keep = []
    cursor = 0.0
    for sp in silences:
        s = sp["start"]
        if s - cursor > 0.05:
            keep.append({"start": round(cursor, 3), "end": round(s, 3)})
        cursor = sp["end"] if sp["end"] is not None else duration
    if duration - cursor > 0.05:
        keep.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return keep


def detect_scenes(path: str, threshold: float) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-filter:v",
         f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    return [round(t, 3) for t in times]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autocut", description="suggest cut points")
    ap.add_argument("media")
    ap.add_argument("--mode", choices=["silence", "scene"], default="silence")
    ap.add_argument("--noise", default="-30dB", help="silence threshold")
    ap.add_argument("--min-silence", type=float, default=0.5,
                    help="min silence duration (s) to count as a gap")
    ap.add_argument("--threshold", type=float, default=0.4,
                    help="scene-change score threshold (scene mode)")
    ap.add_argument("--out", default=None, help="also write suggestions JSON here")
    a = ap.parse_args(argv)

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            _die(f"`{tool}` not found on PATH")
    if not Path(a.media).exists():
        _die(f"media not found: {a.media}")

    duration = probe_duration(a.media)
    if a.mode == "silence":
        silences = detect_silence(a.media, a.noise, a.min_silence)
        result = {"mode": "silence", "media": a.media, "duration": duration,
                  "silences": silences,
                  "keep": keep_segments(duration, silences)}
    else:
        cuts = detect_scenes(a.media, a.threshold)
        result = {"mode": "scene", "media": a.media, "duration": duration,
                  "scene_cuts": cuts}

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
        print(f"[autocut] wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
