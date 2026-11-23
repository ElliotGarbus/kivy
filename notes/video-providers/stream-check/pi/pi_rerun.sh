#!/usr/bin/env bash
# Runs on the Pi (fed through `ssh host bash -s`): restart the stream server
# detached, so it outlives this SSH session, then run stream-check with the
# Kivy that pi_run.sh already built.
set -u
pkill -f "^python3 streams.py"
pkill -f "^ffmpeg .*stream-check/work"
sleep 2
cd ~/stream-check || exit 1
setsid nohup python3 streams.py cityCC0.mp4 > server.log 2>&1 < /dev/null &
for i in $(seq 90); do
  grep -q "Ctrl-C stops" server.log && break
  sleep 1
done
tail -3 server.log

R=$HOME/stream-check-run
DEPS=$HOME/kivy-src/kivy-dependencies
export LD_LIBRARY_PATH="$DEPS/dist/lib:$DEPS/dist/lib64"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export WAYLAND_DISPLAY=wayland-0 SDL_VIDEO_DRIVER=wayland
echo "== running stream-check ($(date +%T))"
cd "$R/src"
KIVY_VIDEO=gstplayer SCHECK_HOST=10.168.168.202 timeout 2400 \
  "$HOME/kivy3-stream-venv/bin/python" main.py > "$R/pi-gstreamer.log" 2>&1
echo "exit $?"
sed 's/\x1b\[[0-9;]*m//g' "$R/pi-gstreamer.log" \
  | grep -E 'GStreamer .* from|render path|SCHECK (STEP_FAIL|CASE_END|DONE)|SCHECK INFO .* (load_s|fps|error_after_s|error_after_drop_s|replay_s)|Traceback|Bail out' \
  | cut -c1-300
echo "== done ($(date +%T)); server keeps running (server.log)"
