#!/usr/bin/env bash
# Runs on the Pi (fed through `ssh host 'bash -s -- CASE...'`): run each
# stream-check case in its own process, with faulthandler, so one crash
# doesn't stop the rest. The stream server must already be running.
set -u
R=$HOME/stream-check-run
DEPS=$HOME/kivy-src/kivy-dependencies
strip() { sed 's/\x1b\[[0-9;]*m//g' "$1"; }

echo "== previous run: last steps before the crash"
strip "$R/pi-gstreamer.log" | grep -E 'SCHECK|Fatal|Segmentation' | tail -4

export LD_LIBRARY_PATH="$DEPS/dist/lib:$DEPS/dist/lib64"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export WAYLAND_DISPLAY=wayland-0 SDL_VIDEO_DRIVER=wayland
export PYTHONFAULTHANDLER=1 KIVY_VIDEO=gstplayer SCHECK_HOST=10.168.168.202
cd "$R/src"
for c in "$@"; do
  log=$R/case-$c.log
  SCHECK_ONLY=$c timeout 600 "$HOME/kivy3-stream-venv/bin/python" main.py > "$log" 2>&1
  rc=$?
  echo "== $c exit $rc ($(date +%T))"
  strip "$log" | grep -E 'render path|SCHECK (STEP_FAIL|CASE_END)|SCHECK INFO .* (load_s|fps|error_after_s|error_after_drop_s|replay_s)|Bail out' | cut -c1-300
  if [ "$rc" -ne 0 ]; then
    strip "$log" | grep -E 'SCHECK STEP|Fatal Python|File "|Thread 0x|Current thread|Segmentation|Traceback' | tail -40 | cut -c1-300
  fi
done
echo "== done ($(date +%T))"
