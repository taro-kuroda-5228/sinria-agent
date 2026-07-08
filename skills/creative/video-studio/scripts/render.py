#!/usr/bin/env python3
"""video-studio: compile a project.json timeline to an MP4 with ffmpeg.

This is the "render" half of the Palmier-style loop. It reads the timeline JSON
(the single source of truth) and builds one ffmpeg filter_complex that:
  - trims each video clip to [in, out], scales/pads to the canvas, normalizes fps,
  - normalizes / synthesizes audio per clip (silent track if a clip has none),
  - concatenates clips in `start` order,
  - draws each caption (drawtext, time-gated via enable=between),
  - stamps an AI-generated label into the file metadata (EU AI Act §50 friendly),
  - optionally burns a visible "AI-generated" disclosure (--disclose).

Dependencies: python3 stdlib + ffmpeg on PATH.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AI_LABEL = "Generated/edited with AI (Sinria video-studio)"

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _die(msg: str, code: int = 1):
    print(f"[render] error: {msg}", file=sys.stderr)
    sys.exit(code)


def find_font() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def pos_expr(pos: str) -> tuple[str, str]:
    x = "(w-text_w)/2"
    if pos == "top":
        y = "60"
    elif pos == "center":
        y = "(h-text_h)/2"
    else:  # bottom
        y = "h-text_h-60"
    return x, y


def build(doc: dict, output: str, disclose: bool, tmpdir: Path) -> list[str]:
    cv = doc["canvas"]
    W, H, FPS = cv["width"], cv["height"], cv["fps"]
    library = doc["library"]
    clips = sorted(doc["tracks"]["video"], key=lambda c: c.get("start", 0.0))
    if not clips:
        _die("no video clips in timeline")

    inputs: list[str] = []
    parts: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []

    for i, clip in enumerate(clips):
        asset = library.get(clip["src"])
        if not asset:
            _die(f"clip {clip['id']} references missing library item {clip['src']}")
        path = asset["path"]
        if not Path(path).exists():
            _die(f"media file missing: {path}")
        inn = float(clip.get("in", 0.0))
        out = float(clip.get("out", asset.get("duration", 0.0)))
        dur = max(0.04, round(out - inn, 3))
        inputs += ["-i", path]
        parts.append(
            f"[{i}:v]trim=start={inn}:end={out},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}[v{i}]"
        )
        if asset.get("has_audio"):
            parts.append(
                f"[{i}:a]atrim=start={inn}:end={out},asetpts=PTS-STARTPTS,"
                f"aresample=44100,aformat=channel_layouts=stereo[a{i}]"
            )
        else:
            parts.append(
                f"anullsrc=r=44100:cl=stereo,atrim=start=0:end={dur},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        vlabels.append(f"[v{i}]")
        alabels.append(f"[a{i}]")

    n = len(clips)
    concat_in = "".join(v + a for v, a in zip(vlabels, alabels))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=1[vc][ac]")

    cur = "[vc]"
    texts = doc["tracks"].get("text", [])
    font = find_font()
    if texts and not font:
        print("[render] warning: no usable font found; captions skipped",
              file=sys.stderr)
        texts = []

    def drawtext(label_in, label_out, text, x, y, size, color, enable=None, box="black@0.45"):
        tf = tempfile.NamedTemporaryFile("w", suffix=".txt", dir=tmpdir,
                                         delete=False, encoding="utf-8")
        tf.write(text)
        tf.close()
        opt = (f"{label_in}drawtext=fontfile='{font}':textfile='{tf.name}':"
               f"x={x}:y={y}:fontsize={size}:fontcolor={color}:"
               f"box=1:boxcolor={box}:boxborderw=10")
        if enable:
            opt += f":enable='{enable}'"
        opt += label_out
        return opt

    for j, t in enumerate(texts):
        x, y = pos_expr(t.get("pos", "bottom"))
        nxt = f"[vt{j}]"
        parts.append(drawtext(cur, nxt, t["text"], x, y,
                              t.get("size", 48), t.get("color", "white"),
                              enable=f"between(t,{t['start']},{t['end']})"))
        cur = nxt

    if disclose and font:
        parts.append(drawtext(cur, "[vd]", "AI-generated",
                              "w-text_w-24", "24", 26, "white", box="red@0.55"))
        cur = "[vd]"

    filtergraph = ";".join(parts)
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filtergraph,
           "-map", cur, "-map", "[ac]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-movflags", "+faststart",
           "-metadata", f"comment={AI_LABEL}",
           "-metadata", "generator=sinria-video-studio",
           output]
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="render", description="compile timeline -> MP4")
    ap.add_argument("project")
    ap.add_argument("--output", "-o", default="out.mp4")
    ap.add_argument("--disclose", action="store_true",
                    help="burn a visible 'AI-generated' disclosure overlay")
    ap.add_argument("--print-cmd", action="store_true",
                    help="print the ffmpeg command and exit (no render)")
    a = ap.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        _die("`ffmpeg` not found on PATH")
    doc = json.loads(Path(a.project).read_text())

    with tempfile.TemporaryDirectory() as td:
        cmd = build(doc, a.output, a.disclose, Path(td))
        if a.print_cmd:
            print(" ".join(cmd))
            return
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr[-2000:])
            _die(f"ffmpeg failed (exit {proc.returncode})", proc.returncode)
    print(f"[render] wrote {a.output}  (label: {AI_LABEL})")


if __name__ == "__main__":
    main()
