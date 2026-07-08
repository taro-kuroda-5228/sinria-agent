#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/apps/android-ambient-capture"
APK="$APP_DIR/app/build/outputs/apk/debug/app-debug.apk"
PACKAGE="com.sinria.ambientcapture"
SERVICE="com.sinria.ambientcapture/.AmbientCaptureService"
ACTION_START="com.sinria.ambientcapture.action.START"
ACTION_STOP="com.sinria.ambientcapture.action.STOP"
DURATION_SECONDS="${SINRIA_AMBIENT_SMOKE_SECONDS:-60}"
EXPORT_ROOT="${SINRIA_AMBIENT_EXPORT_ROOT:-$HOME/.sinria/private/ambient-capture/android-smoke}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="$EXPORT_ROOT/$STAMP"

export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ROOT_DIR/.sinria/tmp/gradle/gradle-8.10.2/bin:$PATH"

mkdir -p "$OUT_DIR"

cd "$APP_DIR"
if [[ -x ./gradlew ]]; then
  ./gradlew :app:assembleDebug --quiet
else
  gradle :app:assembleDebug --quiet
fi

if [[ ! -f "$APK" ]]; then
  echo "APK not found after build: $APK" >&2
  exit 1
fi

DEVICE_COUNT="$(adb devices | awk 'NR>1 && $2=="device" {count++} END {print count+0}')"
if [[ "$DEVICE_COUNT" -lt 1 ]]; then
  echo "No authorized Android device found. Connect Pixel 8, enable USB debugging, approve the prompt, then rerun." >&2
  exit 2
fi

adb install -r "$APK" >/dev/null
adb shell pm grant "$PACKAGE" android.permission.RECORD_AUDIO >/dev/null 2>&1 || true
adb shell pm grant "$PACKAGE" android.permission.POST_NOTIFICATIONS >/dev/null 2>&1 || true
adb shell am start -n "$PACKAGE/.MainActivity" >/dev/null
adb shell am start-foreground-service -n "$SERVICE" -a "$ACTION_START" >/dev/null

echo "Recording for ${DURATION_SECONDS}s with visible Android microphone/notification indicators..."
sleep "$DURATION_SECONDS"
adb shell am start-foreground-service -n "$SERVICE" -a "$ACTION_STOP" >/dev/null
sleep 2

# Debug APK permits run-as. Copy only encrypted export bundles and manifests, not temp raw recordings.
adb exec-out run-as "$PACKAGE" sh -c 'cd files && tar cf - export-bundles 2>/dev/null' > "$OUT_DIR/export-bundles.tar" || {
  echo "Could not export debug bundles with run-as. Check app install/debuggable state." >&2
  exit 3
}
(cd "$OUT_DIR" && tar xf export-bundles.tar && rm export-bundles.tar)

LATEST_BUNDLE="$(find "$OUT_DIR/export-bundles" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1 || true)"
if [[ -z "$LATEST_BUNDLE" || ! -f "$LATEST_BUNDLE/manifest.json" ]]; then
  echo "No encrypted capture bundle manifest found under $OUT_DIR" >&2
  exit 4
fi

python "$ROOT_DIR/scripts/sinria_ambient_capture_ingest.py" --bundle "$LATEST_BUNDLE" --local-only --json
