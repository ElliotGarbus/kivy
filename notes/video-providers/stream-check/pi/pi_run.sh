#!/usr/bin/env bash
# Runs on the Pi: build Kivy from the sent tree into its own venv (reusing
# ~/kivy-src's SDL3 dependency build), then run stream-check with gstplayer.
set -u
R=$HOME/stream-check-run
SRC=$HOME/kivy-src-stream
VENV=$HOME/kivy3-stream-venv
DEPS=$HOME/kivy-src/kivy-dependencies
log() { echo "== $* ($(date +%T))"; }

rm -rf "$SRC"; mkdir -p "$SRC"; tar -xf "$R/kivy.tar" -C "$SRC"
[ -x "$VENV/bin/python" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q -U pip "setuptools~=82.0.0" "cython>=0.29.1,<=3.2.0" wheel || exit 1
log "building kivy"
KIVY_DEPS_ROOT="$DEPS" USE_X11=1 KIVY_SPLIT_EXAMPLES=1 \
  "$VENV/bin/pip" install --no-build-isolation --force-reinstall "$SRC" \
  > "$R/kivy-build.log" 2>&1 || { tail -40 "$R/kivy-build.log"; exit 1; }
log "kivy built"

export LD_LIBRARY_PATH="$DEPS/dist/lib:$DEPS/dist/lib64"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
if [ -S "$XDG_RUNTIME_DIR/wayland-0" ]; then
  export WAYLAND_DISPLAY=wayland-0 SDL_VIDEO_DRIVER=wayland
else
  export DISPLAY=:0 SDL_VIDEO_DRIVER=x11
fi
log "running stream-check on $SDL_VIDEO_DRIVER"
cd "$R/src"
KIVY_VIDEO=gstplayer SCHECK_HOST=10.168.168.202 timeout 2400 \
  "$VENV/bin/python" main.py > "$R/pi-gstreamer.log" 2>&1
echo "exit $?"
sed 's/\x1b\[[0-9;]*m//g' "$R/pi-gstreamer.log" \
  | grep -E 'Kivy        \] v|Installed at|Video       \]|VideoGstplayer|SCHECK (STEP_FAIL|CASE_END|DONE)|SCHECK INFO .* (load_s|fps|error_after|error_after_drop_s|replay_s)|Traceback|Bail out|Error' \
  | cut -c1-300
log "done; full log in $R/pi-gstreamer.log"
