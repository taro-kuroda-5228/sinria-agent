# `project.json` timeline format

The timeline is the single editable source of truth. Edit it via `studio.py`
(preferred) or by hand; `render.py` compiles it. Times are in seconds (floats).

## Schema

```jsonc
{
  "name": "promo",
  "canvas": { "width": 1920, "height": 1080, "fps": 30 },
  "library": {
    "m1": {
      "path": "/abs/path/intro.mp4",   // absolute path, resolved on add
      "kind": "video",                  // video | audio
      "duration": 6.0,                  // probed on add
      "has_audio": true,                // probed; drives audio handling at render
      "width": 1920, "height": 1080,
      "ai_generated": true,             // set by `add --ai`; surfaces in --disclose / provenance
      "source": "comfyui"               // provenance label
    }
  },
  "tracks": {
    "video": [
      { "id": "v1", "src": "m1", "in": 0.0, "out": 4.0, "start": 0.0 }
    ],
    "audio": [
      { "id": "a1", "src": "m2", "in": 0.0, "out": 4.0, "start": 0.0 }
    ],
    "text": [
      { "id": "t1", "text": "Sinria", "start": 0.5, "end": 3.0,
        "size": 48, "color": "white", "pos": "bottom" }
    ]
  },
  "meta": { "ai_label": true }
}
```

## Semantics

- **library** maps a media id → file + probed metadata. `studio.py add` runs
  `ffprobe` and fills `duration`, `has_audio`, `width`, `height`.
- **clip** (`tracks.video[]` / `tracks.audio[]`): `src` references a library id;
  `in`/`out` select a sub-range of the source; `start` is its position on the
  output timeline. `add` auto-sets `start` to the end of the track (append).
- **video render order** = clips sorted by `start`, concatenated. (v1 renders a
  single video track as a sequential concat; crossfades/overlays are a future
  extension — keep one clip per time slot for now.)
- **audio**: each video clip contributes its own audio; clips with `has_audio:false`
  get a silent track of matching length so concat stays aligned.
- **text** (`pos`: `top` | `center` | `bottom`): drawn with a time gate
  (`enable=between(t,start,end)`), centered horizontally with a semi-opaque box.

## Editing recipes

```bash
studio.py trim project.json v2 --in 1.5 --out 6.0   # keep 1.5..6.0s of the source
studio.py move project.json v2 --start 4.0          # reposition on the timeline
studio.py caption project.json --text "..." --start 4 --end 7 --pos top --size 64
```

To assemble an auto-cut: run `autocut.py`, then `add` each `keep` segment of the
same source with `--in`/`--out`.
