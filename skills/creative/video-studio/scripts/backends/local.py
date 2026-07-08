#!/usr/bin/env python3
"""video-studio: local generation dispatcher.

We do NOT reimplement clip generation. Sinria already has generator skills; this
backend just routes an intent to the right existing skill and prints the
recommended way to invoke it. The resulting clip is then added to the timeline
with `studio.py add --ai --source <skill>`.

Usage:
  local.py suggest --intent "explainer for a sorting algorithm"
  local.py suggest --kind math|ascii|ai-video|generative|design
  local.py list
"""
from __future__ import annotations

import argparse
import json
import sys

# kind -> (skill, what it's good for, how to bring the result back)
SKILLS = {
    "math": {
        "skill": "manim-video",
        "good_for": "math/algorithm explainers, 3Blue1Brown-style derivations",
        "then": "studio.py add project.json out.mp4 --ai --source manim-video",
    },
    "ascii": {
        "skill": "ascii-video",
        "good_for": "retro/terminal/text-art video, audio-reactive ASCII visualizers",
        "then": "studio.py add project.json out.mp4 --ai --source ascii-video",
    },
    "ai-video": {
        "skill": "comfyui",
        "good_for": "AI text/image-to-video (Wan, Hunyuan, AnimateDiff) generated footage",
        "then": "studio.py add project.json out.mp4 --ai --source comfyui",
    },
    "generative": {
        "skill": "p5js",
        "good_for": "generative/procedural motion graphics, creative coded sketches",
        "then": "studio.py add project.json out.mp4 --ai --source p5js",
    },
    "design": {
        "skill": "touchdesigner-mcp",
        "good_for": "real-time/audio-reactive visuals, VJ-style or installation footage",
        "then": "studio.py add project.json out.mp4 --ai --source touchdesigner-mcp",
    },
}

KEYWORDS = {
    "math": ["math", "algorithm", "equation", "formula", "derivation", "数式", "アルゴリズム"],
    "ascii": ["ascii", "terminal", "retro", "text art", "matrix"],
    "ai-video": ["ai video", "text-to-video", "image-to-video", "footage", "b-roll",
                 "生成", "実写風", "wan", "hunyuan"],
    "generative": ["generative", "procedural", "motion graphic", "sketch", "particles"],
    "design": ["real-time", "vj", "audio-reactive", "installation", "visuals"],
}


def infer_kind(intent: str) -> str:
    low = intent.lower()
    best, score = "generative", 0
    for kind, words in KEYWORDS.items():
        s = sum(1 for w in words if w in low)
        if s > score:
            best, score = kind, s
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(prog="local", description="route to existing generator skills")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sg = sub.add_parser("suggest")
    sg.add_argument("--kind", choices=list(SKILLS))
    sg.add_argument("--intent", default=None)
    sg.add_argument("--json", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for kind, info in SKILLS.items():
            print(f"{kind:11} -> {info['skill']:18} {info['good_for']}")
        return

    kind = a.kind or (infer_kind(a.intent) if a.intent else None)
    if not kind:
        print("[local] provide --kind or --intent", file=sys.stderr)
        sys.exit(1)
    info = SKILLS[kind]
    if a.json:
        print(json.dumps({"kind": kind, **info}, indent=2, ensure_ascii=False))
    else:
        print(f"intent kind : {kind}")
        print(f"use skill   : {info['skill']}  ({info['good_for']})")
        print(f"then add    : {info['then']}")
        print("\nGenerate the clip with that skill, then add it to the timeline "
              "with the command above (note --ai so it gets the §50 label).")


if __name__ == "__main__":
    main()
