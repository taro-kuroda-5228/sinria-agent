---
name: video-studio
description: "Use when creating or editing video with an agent: assemble clips on a JSON timeline, auto-cut on silence/scene, burn captions, render with ffmpeg, or generate personalized/in-product video via HeyGen/Synthesia/Creatomate APIs. Headless, Palmier-style generate→edit→render loop."
version: 1.0.0
author: [Taro Kuroda, Claude]
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  commands: [ffmpeg, ffprobe]
  env_vars: [HEYGEN_API_KEY, SYNTHESIA_API_KEY, CREATOMATE_API_KEY]
metadata:
  hermes:
    tags: [video, video-editing, timeline, ffmpeg, autocut, captions, heygen, synthesia, creatomate, ai-video, personalized-video, compliance]
    related_skills: [comfyui, manim-video, ascii-video, p5js, touchdesigner-mcp, youtube-content]
    category: creative
---

# video-studio

An **agent-driven video studio**. It reproduces the UX that makes Palmier Pro
compelling — *generate a clip, place it on a timeline, trim it, regenerate, finish
the cut, all in one project* — but **headless and scriptable**, so Sinria
(cron / gateway / Linux) and Claude Code can both drive it with no GUI and no
external editor app.

## When to use

Use when the task is to **edit or assemble video**, not just generate a single
clip:

- "Cut this talk down to the parts where someone is actually speaking"
- "Stitch these clips together with captions and render an MP4"
- "Make a personalized onboarding video per customer from a template" (API backend)
- "Turn this long recording into short vertical clips"
- "Build a promo: an AI-generated intro clip + b-roll + a captioned outro"

For generating a *single* clip from scratch, defer to the existing generator
skills (see **Relationship to other skills**) — this skill **composes** them.

## The loop (what you actually do)

The timeline is a single `project.json` — the editable source of truth (Palmier's
timeline, as JSON). Every edit is just a mutation of that file; `render.py`
compiles it to MP4. The agent IS the chat: natural-language requests become these
commands.

```bash
S=scripts                       # from the skill dir
python $S/studio.py new promo --out project.json --width 1920 --height 1080 --fps 30
python $S/studio.py add project.json intro.mp4 --ai --source comfyui   # generated clip
python $S/studio.py add project.json broll.mp4                          # real footage
python $S/studio.py caption project.json --text "Sinria" --start 0.5 --end 3 --pos bottom
python $S/studio.py trim project.json v2 --in 1.0 --out 6.5            # tighten a clip
python $S/studio.py list project.json                                  # inspect timeline
python $S/render.py project.json --output promo.mp4                    # compile (+ AI label)
```

Regenerate-and-replace = generate a new clip, `add` it, `move`/`trim` to taste,
`render` again. Same project, no context switch.

## Auto-cut (Opus-Clip-style)

```bash
python scripts/autocut.py talk.mp4 --mode silence --noise=-30dB --min-silence 0.5
python scripts/autocut.py talk.mp4 --mode scene  --threshold 0.4
```

Outputs JSON: `silences` + the complementary `keep` segments (silence mode), or
`scene_cuts` (scene mode). It only *suggests* — feed the segments you want back
into `studio.py add ... --in <start> --out <end>`.

## Generation backends (pluggable)

- **Local** — `scripts/backends/local.py suggest --intent "..."` routes an intent
  to the right existing generator skill (manim-video / ascii-video / comfyui /
  p5js / touchdesigner-mcp). Generate there, then `studio.py add --ai --source <skill>`.
- **API** — `scripts/backends/api_gen.py` produces clips via HeyGen (avatar),
  Synthesia (`fromTemplate` + variables + webhook), or Creatomate (data→video).
  This is the **in-product / personalized video** path. Always supports `--dry-run`
  (prints the exact request payload; no network). See `references/backends.md`.

```bash
python scripts/backends/api_gen.py --dry-run synthesia \
  --template-id <id> --var name=Taro --var plan=Pro --webhook https://app/api/hook
```

## Governance & compliance (Sinria invariant — do not skip)

- **Before any external send**, `scripts/govern.py check <text>` scans for
  secrets / PII / PHI. Exit 2 = flagged; `api_gen.py` runs this automatically in
  real mode and aborts on a flag. Never put credentials, patient data, or raw
  private context into a script sent to HeyGen/Synthesia/Creatomate.
- **Every output is AI-labeled**: `render.py` stamps machine-readable metadata
  (`comment=Generated/edited with AI...`); `--disclose` burns a visible
  "AI-generated" overlay. This is aligned with **EU AI Act §50** (machine-readable
  marking + deepfake disclosure, in force 2026-08-02). `govern.py label` adds the
  marker to any existing MP4. See `references/compliance.md`.

## Verify

```bash
bash scripts/smoke_test.sh      # network-free: synthesizes samples, renders, asserts
```

Expected: ends with `PASS: video-studio smoke test`.

## Files

- `scripts/studio.py` — timeline CLI (new/add/caption/trim/move/list)
- `scripts/render.py` — `project.json` → ffmpeg → MP4 (+ AI label, `--disclose`)
- `scripts/autocut.py` — silence/scene cut suggestions
- `scripts/govern.py` — redaction gate + §50 labeling
- `scripts/backends/api_gen.py` — HeyGen / Synthesia / Creatomate (`--dry-run`)
- `scripts/backends/local.py` — dispatcher to existing generator skills
- `references/timeline-format.md` — `project.json` schema + examples
- `references/backends.md` — which backend for which use case (from research)
- `references/compliance.md` — labeling + confidentiality rules

## Relationship to other skills (no duplication)

This skill is the **editor / assembler / orchestrator**. The generators stay in
their own skills and are called as backends — this skill never reimplements them:

| Need | Skill |
|------|-------|
| AI text/image-to-video footage | `comfyui` |
| Math / algorithm explainer | `manim-video` |
| ASCII / retro text-art video | `ascii-video` |
| Generative / procedural motion | `p5js` |
| Real-time / audio-reactive visuals | `touchdesigner-mcp` |
| YouTube transcript → text | `youtube-content` |
| **Cut / trim / caption / assemble / render / personalized API video** | **`video-studio`** |
