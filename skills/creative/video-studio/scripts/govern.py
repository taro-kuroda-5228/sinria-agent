#!/usr/bin/env python3
"""video-studio: governance gate (Sinria invariant).

Two jobs:
  check INPUT   -- scan a string or file for secrets / PII / PHI-ish patterns.
                   Exit 0 = clean, exit 2 = flagged. MUST be run before sending
                   any script/text to an external generation backend.
  label IN.mp4  -- stamp an AI-generated marker into the file metadata
                   (EU AI Act §50: machine-readable "artificially generated"
                   marker). With --disclose it re-encodes with a visible overlay.

This is intentionally conservative: it is a tripwire, not a full DLP system.
Dependencies: python3 stdlib + ffmpeg (only for `label`).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

AI_LABEL = "Generated/edited with AI (Sinria video-studio)"

# (name, regex) — kept deliberately tight to limit false positives.
PATTERNS = [
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("jp_phone", r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b"),
    ("intl_phone", r"\+\d{1,3}[ -]?\(?\d{2,4}\)?[ -]?\d{3,4}[ -]?\d{3,4}"),
    ("credit_card", r"\b(?:\d[ -]?){13,16}\b"),
    ("openai_key", r"\bsk-[A-Za-z0-9]{20,}\b"),
    ("aws_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("phi_hint", r"(?i)\b(patient|診察|患者|carte|医療記録|病歴|診断名)\b"),
    ("mynumber", r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
]


def scan(text: str) -> list[dict]:
    findings = []
    for name, rx in PATTERNS:
        for m in re.finditer(rx, text):
            findings.append({"type": name, "match": _mask(m.group(0))})
    return findings


def _mask(s: str) -> str:
    s = s.strip()
    if len(s) <= 6:
        return s[0] + "***"
    return s[:3] + "***" + s[-2:]


def cmd_check(a):
    src = a.input
    p = Path(src)
    text = p.read_text(errors="ignore") if p.exists() else src
    findings = scan(text)
    if findings:
        print("[govern] FLAGGED — do NOT send to external backend:", file=sys.stderr)
        for f in findings:
            print(f"  - {f['type']}: {f['match']}", file=sys.stderr)
        sys.exit(2)
    print("[govern] clean")


def cmd_label(a):
    if shutil.which("ffmpeg") is None:
        print("[govern] error: ffmpeg not found", file=sys.stderr)
        sys.exit(1)
    inp = a.input
    out = a.output or (str(Path(inp).with_suffix("")) + ".labeled.mp4")
    if a.disclose:
        font = next((f for f in [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ] if Path(f).exists()), None)
        vf = (f"drawtext=fontfile='{font}':text='AI-generated':x=w-text_w-24:y=24:"
              f"fontsize=26:fontcolor=white:box=1:boxcolor=red@0.55:boxborderw=10") if font else None
        cmd = ["ffmpeg", "-y", "-i", inp]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:a", "copy", "-metadata", f"comment={AI_LABEL}",
                "-metadata", "generator=sinria-video-studio", out]
    else:
        cmd = ["ffmpeg", "-y", "-i", inp, "-c", "copy",
               "-metadata", f"comment={AI_LABEL}",
               "-metadata", "generator=sinria-video-studio", out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-1500:])
        sys.exit(proc.returncode)
    print(f"[govern] labeled -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="govern", description="redaction gate + AI labeling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("input"); c.set_defaults(func=cmd_check)
    l = sub.add_parser("label"); l.add_argument("input")
    l.add_argument("--output", "-o", default=None)
    l.add_argument("--disclose", action="store_true")
    l.set_defaults(func=cmd_label)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
