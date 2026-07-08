# Compliance & confidentiality

Two non-negotiables for any AI video produced through this skill.

## 1. Confidentiality (Sinria invariant)

- **Never** put secrets, credentials, PHI/PII, raw patient data, or raw private
  context into a script/template/variable that is sent to an external backend
  (HeyGen, Synthesia, Creatomate).
- `scripts/govern.py check <text-or-file>` is the tripwire (emails, phone numbers,
  card-like numbers, API keys, bearer tokens, private keys, PHI hints, My Number).
  Exit `2` = flagged. `api_gen.py` runs it automatically before any real send and
  aborts on a flag.
- It is a tripwire, not a full DLP. Human judgment still applies for confidential
  or clinical content — when in doubt, generate locally (comfyui/manim/etc.) and
  keep the asset on-prem.

## 2. AI labeling — EU AI Act §50 (in force 2026-08-02)

The research verified two obligations that can reach an API-driven SaaS:

- **§50(2) — providers** of AI generating synthetic audio/image/**video**/text
  must mark outputs in a **machine-readable** format, detectable as artificially
  generated.
- **§50(4) — deployers** generating/manipulating **deepfake** image/audio/video
  must **disclose** that the content is artificially generated/manipulated.

How this skill helps:

- `render.py` always stamps machine-readable metadata
  (`comment=Generated/edited with AI (Sinria video-studio)`, `generator=...`).
- `render.py --disclose` (and `govern.py label --disclose`) burns a visible
  "AI-generated" overlay for the deployer-disclosure case.
- `govern.py label IN.mp4` adds the marker to any pre-existing MP4.

Caveats:

- This is **engineering support for compliance, not legal advice.** Exemptions
  exist (standard editing aids, artistic/satirical works, law enforcement).
- **Japan-specific** rules (肖像権・パブリシティ権, 景表法上のAI表示, domestic
  deepfake regulation) were an open question in the research — confirm with legal
  before any public, person-likeness, or advertising use.
- Metadata can be stripped by re-encoding/upload pipelines; for strong provenance
  consider a C2PA/Content Credentials step in addition to this marker.
