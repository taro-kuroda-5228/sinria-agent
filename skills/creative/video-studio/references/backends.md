# Generation backends — which to use when

Grounded in the 2026-06 research (`video-production-research.md`).
Split generation into **"avatar/full-AI generation"** vs **"template/data
synthesis"** — most product use cases end up using both as two layers.

## Decision table

| Goal | Backend | Why |
|------|---------|-----|
| Talking-head avatar + narration, personalized per user | **HeyGen** API | Avatar uploaded once, reused across all videos; no re-shoot (Pyne AI pattern) |
| Personalized video from a pre-built template + variables, async webhook | **Synthesia** `fromTemplate` | `templateData` injection + `video.completed` webhook → clean in-product flow |
| Data-driven mass variants (name/number/logo swaps), no avatar | **Creatomate** | Cheap, fast template render API for bulk personalization |
| Math / algorithm explainer footage | local → **manim-video** | already in Sinria |
| AI text/image-to-video b-roll (Wan/Hunyuan/AnimateDiff) | local → **comfyui** | already in Sinria |
| Retro / ASCII / terminal aesthetic | local → **ascii-video** | already in Sinria |
| Generative / procedural motion graphics | local → **p5js** | already in Sinria |

Rule of thumb: **avatar/spokesperson = generation API (HeyGen/Synthesia); pure
field swaps = synthesis API (Creatomate/templates).**

## API backend usage (`scripts/backends/api_gen.py`)

All commands accept `--dry-run` (print the exact JSON payload, no network). Real
mode requires the matching env key and runs the redaction gate first.

```bash
# HeyGen — avatar + scripted narration
python scripts/backends/api_gen.py --dry-run heygen \
  --avatar <avatar_id> --script "Welcome to Sinria, {{name}}." --voice <voice_id>

# Synthesia — template + variables + completion webhook (personalized, async)
python scripts/backends/api_gen.py --dry-run synthesia \
  --template-id <id> --var name=Taro --var plan=Pro \
  --webhook https://app.example.com/api/synthesia-hook

# Creatomate — data→video template render
python scripts/backends/api_gen.py --dry-run creatomate \
  --template-id <id> --mod "Name=Taro" --mod "Price=¥980"
```

Endpoints used (verified 2026-06):
- HeyGen `POST https://api.heygen.com/v2/video/generate` (header `X-Api-Key`)
- Synthesia `POST https://api.synthesia.io/v2/videos/fromTemplate` (Bearer)
- Creatomate `POST https://api.creatomate.com/v1/renders` (Bearer)

> Synthesia payloads default `test: true` (watermarked draft). Drop it only when
> the user explicitly approves a production render.

## In-product / personalized pattern (the research's #1 case)

1. Author a template once (Synthesia Studio / Creatomate / a HeyGen avatar).
2. From your product: gather per-user variables → `govern.py check` them.
3. Call the API backend with those variables; subscribe to the completion webhook.
4. Store the returned video URL/id; the §50 AI label travels with it.

This mirrors Pyne AI × HeyGen (avatar reused across all videos, instant updates,
no re-shoot) — see the research note for the verified architecture vs the
vendor-claimed (unverified) outcome numbers.
