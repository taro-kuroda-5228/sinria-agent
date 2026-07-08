#!/usr/bin/env python3
"""video-studio: timeline CLI.

The timeline is a single JSON file (`project.json`) that acts as the editable
"source of truth" — the same role Palmier's timeline plays, but headless. Every
edit operation here just mutates that JSON; `render.py` compiles it to MP4.

Commands:
  new     PROJECT_NAME [--out project.json] [--width --height --fps]
  add     project.json MEDIA [--track video|audio] [--in S --out S] [--ai] [--source NAME]
  caption project.json --text TXT [--start S --end S] [--size N --color C --pos top|center|bottom]
  trim    project.json CLIP_ID [--in S --out S]
  move    project.json CLIP_ID --start S
  list    project.json

Dependencies: python3 stdlib + ffprobe on PATH.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _die(msg: str, code: int = 1):
    print(f"[studio] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _need(tool: str):
    if shutil.which(tool) is None:
        _die(f"`{tool}` not found on PATH (install ffmpeg).")


def load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        _die(f"project not found: {path}")
    return json.loads(p.read_text())


def save(path: str, doc: dict):
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def ffprobe_media(path: str) -> dict:
    """Return {duration, has_audio, width, height} for a media file."""
    _need("ffprobe")
    if not Path(path).exists():
        _die(f"media not found: {path}")

    def run(args):
        return subprocess.run(["ffprobe", "-v", "error", *args, path],
                              capture_output=True, text=True).stdout.strip()

    duration = run(["-show_entries", "format=duration", "-of", "default=nk=1:nw=1"])
    audio = run(["-select_streams", "a", "-show_entries", "stream=index",
                 "-of", "csv=p=0"])
    wh = run(["-select_streams", "v:0", "-show_entries", "stream=width,height",
              "-of", "csv=p=0:s=x"])
    try:
        dur = float(duration) if duration else 0.0
    except ValueError:
        dur = 0.0
    width, height = (0, 0)
    if "x" in wh:
        try:
            width, height = (int(x) for x in wh.split("x")[:2])
        except ValueError:
            pass
    return {"duration": round(dur, 3), "has_audio": bool(audio.strip()),
            "width": width, "height": height}


def _next_id(items, prefix) -> str:
    n = 1
    existing = {it["id"] for it in items}
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def _track_end(track) -> float:
    end = 0.0
    for c in track:
        end = max(end, c.get("start", 0.0) + (c.get("out", 0.0) - c.get("in", 0.0)))
    return round(end, 3)


# --- commands ---------------------------------------------------------------

def cmd_new(a):
    doc = {
        "name": a.name,
        "canvas": {"width": a.width, "height": a.height, "fps": a.fps},
        "library": {},
        "tracks": {"video": [], "audio": [], "text": []},
        "meta": {"ai_label": True},
    }
    save(a.out, doc)
    print(f"[studio] created {a.out} ({a.width}x{a.height}@{a.fps})")


def cmd_add(a):
    doc = load(a.project)
    info = ffprobe_media(a.media)
    kind = "audio" if a.track == "audio" else "video"
    media_id = _next_id([{"id": k} for k in doc["library"]], "m")
    doc["library"][media_id] = {
        "path": str(Path(a.media).resolve()),
        "kind": kind,
        "duration": info["duration"],
        "has_audio": info["has_audio"],
        "width": info["width"],
        "height": info["height"],
        "ai_generated": bool(a.ai),
        "source": a.source or "local",
    }
    track = doc["tracks"][kind]
    clip_in = a.in_ if a.in_ is not None else 0.0
    clip_out = a.out if a.out is not None else info["duration"]
    start = _track_end(track) if a.start is None else a.start
    clip_id = _next_id(track, "v" if kind == "video" else "a")
    track.append({"id": clip_id, "src": media_id, "in": clip_in,
                  "out": clip_out, "start": round(start, 3)})
    save(a.project, doc)
    flag = " [AI]" if a.ai else ""
    print(f"[studio] +{kind} clip {clip_id} (src {media_id}{flag}) "
          f"in={clip_in} out={clip_out} start={start}")


def cmd_caption(a):
    doc = load(a.project)
    tid = _next_id(doc["tracks"]["text"], "t")
    doc["tracks"]["text"].append({
        "id": tid, "text": a.text, "start": a.start, "end": a.end,
        "size": a.size, "color": a.color, "pos": a.pos,
    })
    save(a.project, doc)
    print(f"[studio] +text {tid} \"{a.text}\" [{a.start}-{a.end}]")


def _find_clip(doc, clip_id):
    for tname in ("video", "audio"):
        for c in doc["tracks"][tname]:
            if c["id"] == clip_id:
                return c
    _die(f"clip not found: {clip_id}")


def cmd_trim(a):
    doc = load(a.project)
    c = _find_clip(doc, a.clip_id)
    if a.in_ is not None:
        c["in"] = a.in_
    if a.out is not None:
        c["out"] = a.out
    save(a.project, doc)
    print(f"[studio] trim {a.clip_id} -> in={c['in']} out={c['out']}")


def cmd_move(a):
    doc = load(a.project)
    c = _find_clip(doc, a.clip_id)
    c["start"] = a.start
    save(a.project, doc)
    print(f"[studio] move {a.clip_id} -> start={a.start}")


def cmd_list(a):
    doc = load(a.project)
    cv = doc["canvas"]
    print(f"# {doc['name']} ({cv['width']}x{cv['height']}@{cv['fps']})")
    for tname in ("video", "audio"):
        print(f"[{tname}]")
        for c in doc["tracks"][tname]:
            src = doc["library"].get(c["src"], {})
            ai = " AI" if src.get("ai_generated") else ""
            print(f"  {c['id']:>4}  start={c['start']:<6} in={c['in']:<5} "
                  f"out={c['out']:<5} src={c['src']} ({src.get('source','?')}{ai})")
    if doc["tracks"]["text"]:
        print("[text]")
        for t in doc["tracks"]["text"]:
            print(f"  {t['id']:>4}  [{t['start']}-{t['end']}] \"{t['text']}\"")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studio", description="video-studio timeline CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new"); n.add_argument("name")
    n.add_argument("--out", default="project.json")
    n.add_argument("--width", type=int, default=1280)
    n.add_argument("--height", type=int, default=720)
    n.add_argument("--fps", type=int, default=30)
    n.set_defaults(func=cmd_new)

    ad = sub.add_parser("add"); ad.add_argument("project"); ad.add_argument("media")
    ad.add_argument("--track", choices=["video", "audio"], default="video")
    ad.add_argument("--in", dest="in_", type=float, default=None)
    ad.add_argument("--out", type=float, default=None)
    ad.add_argument("--start", type=float, default=None)
    ad.add_argument("--ai", action="store_true", help="mark source as AI-generated")
    ad.add_argument("--source", default=None, help="provenance label, e.g. heygen")
    ad.set_defaults(func=cmd_add)

    cp = sub.add_parser("caption"); cp.add_argument("project")
    cp.add_argument("--text", required=True)
    cp.add_argument("--start", type=float, default=0.0)
    cp.add_argument("--end", type=float, default=3.0)
    cp.add_argument("--size", type=int, default=48)
    cp.add_argument("--color", default="white")
    cp.add_argument("--pos", choices=["top", "center", "bottom"], default="bottom")
    cp.set_defaults(func=cmd_caption)

    tr = sub.add_parser("trim"); tr.add_argument("project"); tr.add_argument("clip_id")
    tr.add_argument("--in", dest="in_", type=float, default=None)
    tr.add_argument("--out", type=float, default=None)
    tr.set_defaults(func=cmd_trim)

    mv = sub.add_parser("move"); mv.add_argument("project"); mv.add_argument("clip_id")
    mv.add_argument("--start", type=float, required=True)
    mv.set_defaults(func=cmd_move)

    ls = sub.add_parser("list"); ls.add_argument("project")
    ls.set_defaults(func=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
