#!/usr/bin/env bash
# Build, install and run the device check on one device, answering the app's
# SHOT and ACTION requests, then save logcat, screenshots and a summary.
#
#   ./run_check.sh SERIAL [ABI]      (ABI: arm64_v8a, the default, or x86_64)
#
# Run make_clips.py and make_wheels.py, and `kivyforge lock -p android`, first.
set -euo pipefail

SERIAL=$1
ABI=${2:-arm64_v8a}
ADB=${ADB:-/c/Android/Sdk/platform-tools/adb.exe}
PKG=org.kivyforge.videoprovidercheck
ACTIVITY=org.kivy.android.PythonActivity
TIMEOUT=${TIMEOUT:-420}

cd "$(dirname "$0")"
OUT=results/$SERIAL
rm -rf "$OUT"
mkdir -p "$OUT"
adb() { "$ADB" -s "$SERIAL" "$@"; }

kivyforge build -p android --debug --abi "$ABI"
adb install -r -g android-provider-check-android/app/build/outputs/apk/debug/app-debug.apk
adb shell am force-stop "$PKG"
adb logcat -c
adb logcat -v time > "$OUT/logcat.txt" &
LOGCAT=$!
trap 'kill $LOGCAT 2>/dev/null || true' EXIT
adb shell am start -n "$PKG/$ACTIVITY" > /dev/null

shots_done=""
backgrounded=0
start=$(date +%s)
while true; do
    for name in $(grep -o 'VCHECK SHOT [a-z0-9_]*' "$OUT/logcat.txt" | awk '{print $3}'); do
        case " $shots_done " in *" $name "*) continue ;; esac
        sleep 1
        adb exec-out screencap -p > "$OUT/$name.png"
        shots_done="$shots_done $name"
        echo "screenshot: $name"
    done
    if [ $backgrounded -eq 0 ] && grep -q 'VCHECK ACTION background' "$OUT/logcat.txt"; then
        backgrounded=1
        adb shell input keyevent KEYCODE_HOME
        sleep 5
        adb shell am start -n "$PKG/$ACTIVITY" > /dev/null
        echo "backgrounded and restored the app"
    fi
    if grep -q 'VCHECK DONE' "$OUT/logcat.txt"; then
        break
    fi
    if [ $(( $(date +%s) - start )) -gt "$TIMEOUT" ]; then
        echo "timed out after ${TIMEOUT}s" | tee -a "$OUT/summary.txt"
        break
    fi
    sleep 1
done

grep -E 'VCHECK|VideoAndroid' "$OUT/logcat.txt" | sed 's/^.*python\.std[a-z]*([ 0-9]*): //' >> "$OUT/summary.txt" || true
cat "$OUT/summary.txt"
