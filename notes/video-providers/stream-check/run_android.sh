#!/usr/bin/env bash
# Build, install and run stream-check on one device; save logcat and the
# SCHECK lines under results/.
#
#   ./run_android.sh SERIAL [ABI]      (ABI: arm64_v8a, the default, or x86_64)
#
# Run `kivyforge lock -p android` first.
set -euo pipefail

SERIAL=$1
ABI=${2:-arm64_v8a}
ADB=${ADB:-/c/Android/Sdk/platform-tools/adb.exe}
PKG=org.kivyforge.streamcheck
ACTIVITY=org.kivy.android.PythonActivity
TIMEOUT=${TIMEOUT:-1500}

cd "$(dirname "$0")"
OUT=results/android-$SERIAL
mkdir -p "$OUT"
adb() { "$ADB" -s "$SERIAL" "$@"; }

kivyforge build -p android --debug --abi "$ABI" > "$OUT/build.txt" 2>&1 \
    || { tail -40 "$OUT/build.txt"; exit 1; }
adb install -r -g stream-check-android/app/build/outputs/apk/debug/app-debug.apk > /dev/null
adb shell am force-stop "$PKG"
adb shell input keyevent KEYCODE_WAKEUP || true
adb logcat -c
adb logcat -v time > "$OUT/logcat.txt" &
LOGCAT=$!
trap 'kill $LOGCAT 2>/dev/null || true' EXIT
adb shell am start -n "$PKG/$ACTIVITY" > /dev/null

start=$(date +%s)
while ! grep -q 'SCHECK DONE' "$OUT/logcat.txt"; do
    if [ $(( $(date +%s) - start )) -gt "$TIMEOUT" ]; then
        echo "timed out after $TIMEOUT s"
        break
    fi
    if ! adb shell pidof "$PKG" > /dev/null 2>&1 && [ $(( $(date +%s) - start )) -gt 30 ]; then
        echo "app is no longer running"
        break
    fi
    sleep 5
done
sleep 2
grep -o 'SCHECK .*' "$OUT/logcat.txt" > "$OUT/scheck.txt" || true
grep -E 'STEP_FAIL|DONE' "$OUT/scheck.txt" || true
