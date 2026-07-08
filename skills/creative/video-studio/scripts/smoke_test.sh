#!/usr/bin/env bash
# video-studio smoke test — network-free, synthesizes its own sample media.
# Exercises: studio (new/add/caption) -> render -> metadata/duration asserts,
# autocut (silence), govern (check flag + label). Exits 0 with PASS, non-zero on failure.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
command -v ffmpeg  >/dev/null || fail "ffmpeg not found"
command -v ffprobe >/dev/null || fail "ffprobe not found"

echo "== generating sample clips =="
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc=size=320x240:rate=30:duration=2" \
  -f lavfi -i "sine=frequency=300:duration=2" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$TMP/clipA.mp4"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x240:rate=30:duration=2" \
  -f lavfi -i "sine=frequency=600:duration=2" \
  -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$TMP/clipB.mp4"
# clipC: tone(1s) - silence(1s) - tone(1s) for autocut
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc=size=320x240:rate=30:duration=3" \
  -filter_complex "sine=frequency=440:duration=1[t0];anullsrc=r=44100:cl=mono:d=1[s0];sine=frequency=880:duration=1[t1];[t0][s0][t1]concat=n=3:v=0:a=1[aout]" \
  -map 0:v -map "[aout]" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$TMP/clipC.mp4"

echo "== studio: new/add/caption =="
PROJ="$TMP/project.json"
"$PY" "$HERE/studio.py" new smoke --out "$PROJ" --width 640 --height 360 --fps 30
"$PY" "$HERE/studio.py" add "$PROJ" "$TMP/clipA.mp4"
"$PY" "$HERE/studio.py" add "$PROJ" "$TMP/clipB.mp4"
"$PY" "$HERE/studio.py" caption "$PROJ" --text "Sinria video-studio" --start 0.5 --end 3.5
"$PY" "$HERE/studio.py" list "$PROJ"

echo "== render =="
OUT="$TMP/out.mp4"
"$PY" "$HERE/render.py" "$PROJ" --output "$OUT"
[ -f "$OUT" ] || fail "render produced no file"

DUR="$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$OUT")"
echo "rendered duration: ${DUR}s (expect ~4)"
awk -v d="$DUR" 'BEGIN{exit !(d>3.6 && d<4.4)}' || fail "unexpected duration: $DUR"

COMMENT="$(ffprobe -v error -show_entries format_tags=comment -of default=nk=1:nw=1 "$OUT" || true)"
echo "metadata comment: $COMMENT"
case "$COMMENT" in *AI*) : ;; *) fail "AI label metadata missing" ;; esac

echo "== autocut (silence) =="
SUG="$("$PY" "$HERE/autocut.py" "$TMP/clipC.mp4" --noise=-30dB --min-silence 0.4)"
echo "$SUG"
echo "$SUG" | grep -q '"silences"' || fail "autocut produced no silences key"
python3 - "$SUG" <<'PYEOF'
import json,sys
d=json.loads(sys.argv[1])
assert d.get("silences"), "no silence detected in clipC"
assert d.get("keep"), "no keep segments computed"
print(f"autocut: {len(d['silences'])} silence span(s), {len(d['keep'])} keep segment(s)")
PYEOF

echo "== govern: check (should flag) =="
if "$PY" "$HERE/govern.py" check "reach me at test@example.com"; then
  fail "govern check did not flag an email"
else
  echo "govern correctly flagged PII (exit nonzero)"
fi
"$PY" "$HERE/govern.py" check "a perfectly clean sentence" || fail "govern flagged clean text"

echo "== govern: label =="
LBL="$TMP/labeled.mp4"
"$PY" "$HERE/govern.py" label "$OUT" --output "$LBL"
[ -f "$LBL" ] || fail "govern label produced no file"

echo "== api_gen: dry-run =="
"$PY" "$HERE/backends/api_gen.py" --dry-run synthesia --template-id demo --var name=Taro | grep -q "templateData" || fail "synthesia dry-run payload missing"

echo
echo "PASS: video-studio smoke test"
