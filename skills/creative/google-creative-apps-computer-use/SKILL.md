---
name: google-creative-apps-computer-use
description: Create Google images and videos through visible app UIs.
version: 1.0.0
author: Taro Kuroda, Sinria Agent
license: MIT
platforms: [macos]
metadata:
  sinria:
    tags: [computer-use, google, imagen-2, nano-banana-2, gemini-omni, image-generation, video-generation, gui-automation]
    related_skills: [macos-computer-use, video-studio, sinria-video-editing-workflow]
---

# Google Creative Apps Computer Use Skill

Automate image and video generation in already-authenticated Google creative
applications using macOS `computer_use` for all application interaction. This
skill never calls a generation API or silently substitutes another model.

The supported requested routes are:

- **Imagen 2** — image generation in the application where the exact `Imagen 2` label is visible.
- **Nano Banana 2** — image generation/editing in the application where the exact `Nano Banana 2` label is visible.
- **Gemini Omni** — multimodal image/video generation or editing in the application where the exact `Gemini Omni` label is visible.

This skill intentionally does **not** call Gemini, Vertex AI, Imagen, AI Studio, or any other generation API. It does not type API keys, inspect cookies, export browser credentials, or silently substitute another model.

The real acceptance condition is not “Generate was clicked.” It is:

1. the requested application and exact visible model/tool were selected;
2. the requested asset visibly completed;
3. the asset was downloaded through the GUI;
4. the downloaded image/video was located on disk and opened or decoded successfully;
5. representative pixels/frames were inspected against the prompt;
6. the local path and any quality limitation were reported honestly.

## When to Use

Use for requests such as:

- “Nano Banana 2でこの画像を編集して”
- “Imagen 2で16:9のキービジュアルを3案作って”
- “Gemini Omniでこの静止画から短い動画を作って”
- “Googleの契約済みWebアプリを操作して画像・動画生成を自動化して”

Do not use when:

- the user explicitly asks for an API, SDK, batch endpoint, or headless HTTP request;
- the prompt or reference contains PHI, PII, credentials, classified material, or other raw confidential information;
- the exact requested model cannot be confirmed in the visible UI;
- generation requires accepting a new paid plan, purchasing credits, changing account permissions, passing CAPTCHA/2FA, or entering a password. Stop at that human gate.

## Prerequisites

- macOS with the native `computer_use` tool available.
- An already-open, already-authenticated visible Google application.
- The exact requested model label visible in that application's UI.
- Public or sanitized prompts and reference media only.
- Local write access under `~/.sinria/generated-media/`.

### Confidentiality gate

These applications are external services. Before pasting a prompt or uploading a file:

1. classify the prompt and every reference as `public`, `sanitized`, or `confidential`;
2. proceed only with `public` or adequately `sanitized` material;
3. exclude names, dates of birth, medical record numbers, faces tied to identity, patient screenshots, internal credentials, unpublished confidential documents, and hidden metadata;
4. if sanitization would materially change the requested output, stop and state the exact field or asset that blocks external use.

Never include raw PHI/PII in screenshots, logs, manifests, filenames, or external prompts.

## How to Run

Invoke the skill with the requested route, operation, prompt, references, count,
and output constraints. Use `computer_use(action="list_apps")`, then scoped SOM
captures and fresh element indices for every GUI transition. Download through
the visible application and verify the resulting local file with
`vision_analyze` or `video_analyze` before reporting completion.

## Quick Reference

| Route | Primary operations | Required UI evidence |
|---|---|---|
| Imagen 2 | text-to-image | Exact `Imagen 2` label |
| Nano Banana 2 | text-to-image, image-edit | Exact `Nano Banana 2` label |
| Gemini Omni | image/video generation or editing | Exact `Gemini Omni` label and media mode |

All routes use visible GUI interaction only. Never call Gemini, Vertex AI,
Imagen, AI Studio, or another generation API as a fallback.

## Procedure

### 1. Normalize the job

Establish these fields from the request, using sensible defaults when omitted:

```yaml
provider_route: imagen-2 | nano-banana-2 | gemini-omni
operation: text-to-image | image-edit | text-to-video | image-to-video | video-edit
prompt: public-or-sanitized text
reference_files: []
count: 1
aspect_ratio: 16:9 for video, 1:1 for image unless context clearly implies otherwise
duration: application default unless the user specifies one
output_root: ~/.sinria/generated-media/<YYYY-MM-DD>/<short-job-slug>/
```

Do not invent a model substitution. `Nano Banana 2` is not interchangeable with another Nano Banana release; `Imagen 2` is not interchangeable with a newer Imagen label; `Gemini Omni` is not interchangeable with ordinary Gemini chat.

### 2. Discover the visible application

Start with:

```text
computer_use(action="list_apps")
computer_use(action="capture", mode="som", app="Google Chrome")
```

Use the user's already-open, already-authenticated visible application. Never raise the window unless explicitly requested. Do not inspect unrelated tabs.

If Google Chrome capture is `0x0`, empty, ambiguous, or unavailable:

1. call `list_apps` again;
2. route background focus without raising:
   `computer_use(action="focus_app", app="Google Chrome", raise_window=false)`;
3. recapture once;
4. if the intended application is visibly running under `Google Chrome for Testing`, `Chromium`, Safari, or a native app, capture that exact app instead;
5. if the exact window still cannot be resolved, stop. Do not blind-click coordinates, switch to a sibling window, or use an API as a fallback.

A dedicated visible Chrome profile is acceptable only when it already exists for Sinria and the user is authenticated. Never type passwords, 2FA codes, recovery codes, or cookies.

### 3. Confirm route and model before entering content

Capture with `mode="som"` and verify all available evidence:

- page/app title identifies the intended Google creative application;
- a model/tool selector or visible workspace label contains the exact requested label;
- the operation supports the requested media type;
- no account, payment, CAPTCHA, permission, or safety dialog is blocking the page.

If the exact requested label is not immediately visible, use only normal visible UI controls such as `Tools`, `Model`, `Create`, or dropdowns. Capture after opening each control and select by fresh element index. Never rely on a stale index after a UI change.

If the exact model label remains absent, report `requested model not confirmed in visible UI`. Do not silently continue with “Auto”, “latest”, or a similarly named model.

### 4. Configure the job

For each state-changing action:

1. capture;
2. click by the current element index;
3. capture again or use `capture_after=true`;
4. verify the resulting state.

Set aspect ratio, count, duration, resolution, and operation only when visible and supported. If a requested setting is absent, preserve the application default and disclose that limitation rather than guessing.

For reference uploads:

1. click the visible upload/add-media control;
2. use the macOS file picker through `computer_use`;
3. use `cmd+shift+g`, type the absolute local path, press Return, select the file, and confirm;
4. recapture and verify the filename/thumbnail is attached before generating.

Only upload the explicit reference files. Never browse unrelated folders or choose a similarly named file.

### 5. Enter the prompt

Click the visible prompt field and type only the reviewed public/sanitized prompt. For edits, state what must change and what must remain invariant.

Recommended prompt shape:

```text
Goal: <the intended image/video>
Subject: <main subject>
Composition/motion: <framing, camera, movement>
Style/lighting: <visual direction>
Constraints: <aspect ratio, duration, continuity, anatomy/product fidelity>
Avoid: <artifacts, unwanted text/logos, unsafe or inaccurate elements>
```

Do not add hidden business context, patient context, credentials, or private rationale “to help the model.”

### 6. Generate with bounded waiting

Click the visible `Generate`, `Create`, `Run`, or equivalent control only after the route, model, prompt, references, and settings are verified.

After submission:

- recapture to verify a real queued/generating state;
- wait in bounded intervals (for example 10–30 seconds) and recapture;
- continue while visible progress is advancing;
- if the application reports a concrete error, preserve a sanitized summary and retry at most once only when the error suggests a transient failure;
- do not repeatedly click Generate, which can create duplicate billable jobs;
- do not exceed the requested candidate count.

A safety refusal is not a transient failure. Report it rather than trying evasive prompts.

### 7. Inspect before downloading

When completion is visible:

- confirm that the number and media type match the request;
- inspect the preview for obvious prompt mismatch, malformed text, anatomy/product errors, continuity problems, watermarks, split-screen artifacts, or accidental private content;
- for multiple candidates, keep candidate identities stable (`candidate-01`, `candidate-02`, ...).

Regenerate only when the result clearly fails the requested constraints and the original request authorizes iterative creation. Never claim a candidate is publish-ready based only on a thumbnail.

### 8. Download through the GUI

Use the result card's visible download/export control. Capture immediately afterward and verify a success state or disappearance of the progress indicator.

Create the Sinria-native destination directory locally:

```text
~/.sinria/generated-media/<YYYY-MM-DD>/<short-job-slug>/
```

Find the newly downloaded file by comparing the download directory before and after the GUI action. Do not select “the newest file” without confirming its creation time, extension, and relation to the current job. Copy or move the confirmed artifact into the destination with stable names:

```text
imagen-2-candidate-01.png
nano-banana-2-candidate-01.png
gemini-omni-candidate-01.mp4
```

Retain the original downloaded file unless the user asked for cleanup.

### 9. Verify the real artifact

For images:

- verify the file is non-empty and decodes as an image;
- inspect it with `vision_analyze`;
- confirm dimensions/aspect ratio and representative content;
- disclose visible defects or unmet constraints.

For videos:

- verify the file is non-empty and decodes through the full duration;
- inspect with `video_analyze`;
- confirm duration, dimensions, presence/absence of audio, and real inter-frame motion;
- inspect early, middle, and late content, not only the poster frame;
- if further editing is requested, pass the verified file to `video-studio` or `sinria-video-editing-workflow` without regenerating unnecessarily.

A successful download notification alone is insufficient.

### 10. Record a sanitized manifest

Write `manifest.json` in the job directory without secrets or confidential source text:

```json
{
  "route": "nano-banana-2",
  "operation": "image-edit",
  "application_ui_confirmed": true,
  "model_label_confirmed": "Nano Banana 2",
  "candidate_count": 1,
  "artifacts": ["nano-banana-2-candidate-01.png"],
  "verification": {
    "decoded": true,
    "visual_reviewed": true,
    "limitations": []
  },
  "provenance": "Generated through visible application UI using Sinria computer_use; no generation API used."
}
```

Do not store the full prompt if it contains internal context. A short sanitized summary is sufficient.

## Route-Specific Guidance

### Imagen 2

- Require the exact visible `Imagen 2` label before generation.
- Treat it as an image route unless the UI explicitly offers a supported video operation.
- Prefer generating text-free artwork and adding precise typography locally when the image contains important copy.
- Verify hands, faces, product geometry, logos, and embedded text at full resolution.

### Nano Banana 2

- Require the exact visible `Nano Banana 2` label.
- For edits, upload the source first and verify the attachment preview before adding the edit instruction.
- State preservation constraints explicitly: identity-neutral appearance, composition, background, product shape, or color values as appropriate.
- Compare the output with the source and reject unintended structural drift.

### Gemini Omni

- Require the exact visible `Gemini Omni` label and the requested image/video operation.
- For image-to-video or video edits, verify the correct source asset is attached and the preview matches it.
- Use scene-specific generation for longer videos instead of looping one short clip.
- Verify motion direction, temporal continuity, subject persistence, anatomy/product accuracy, and whether audio was actually produced.
- Do not promote a clinically or technically inaccurate result merely because it has motion.

## Batch Automation

For `count > 1`, process candidates sequentially unless the application visibly provides one bounded multi-candidate generation action. Sequential processing avoids duplicate submissions and makes provenance clearer.

For every candidate:

1. verify current route/model;
2. submit once;
3. wait for completion;
4. inspect and download;
5. verify the local artifact;
6. update the sanitized manifest;
7. proceed to the next candidate.

Stop the batch if the application changes model, signs out, shows a payment/account gate, produces repeated errors, or the results reveal sensitive content.

## Pitfalls

1. **Using an API because GUI capture failed.** This violates the task. Resolve the visible application or report the exact UI blocker.
2. **Assuming a product name from the URL.** Confirm the exact model label in the visible UI every time.
3. **Reusing SOM element indices.** Any capture or UI transition invalidates the old index.
4. **Blind retries creating duplicate charges.** Verify queue/progress state before any retry and retry at most once for a clearly transient error.
5. **Stopping at a preview.** Download and decode the real file.
6. **Picking an unrelated recent download.** Establish before/after download state and confirm the exact artifact.
7. **Leaking confidential context in prompts or filenames.** External tools receive only public/sanitized material.
8. **Calling a technically valid video publish-ready.** Inspect full decode, early/mid/late content, motion, audio, and prompt fidelity.
9. **Clicking payment, password, CAPTCHA, permission, or account dialogs.** Stop for the smallest human-only action.
10. **Silent model substitution.** Missing `Imagen 2`, `Nano Banana 2`, or `Gemini Omni` is a blocker, not permission to use “Auto” or another release.

## Verification

### Completion report

Report concisely:

- requested route and operation;
- exact visible model label confirmed;
- candidate count completed;
- verified local artifact path(s);
- image dimensions or video duration/dimensions/audio state;
- any mismatch, refusal, watermark, or quality limitation;
- explicit statement: `Generation API used: no`.

If blocked, report the exact stop point, visible cause, whether any job was submitted, billing/duplication risk, and the smallest next action.

### Checklist

- [ ] External input classified as public or sanitized
- [ ] Correct visible application confirmed
- [ ] Exact requested model label confirmed
- [ ] Prompt/reference/settings read back before submission
- [ ] Submission happened exactly once per intended candidate
- [ ] Visible completion confirmed
- [ ] Artifact downloaded through GUI
- [ ] Local file uniquely matched to this job
- [ ] Image decoded and visually inspected, or video fully decoded and analyzed
- [ ] Sanitized manifest written under `.sinria/generated-media`
- [ ] No API, credential extraction, hidden browser automation, or silent model substitution
