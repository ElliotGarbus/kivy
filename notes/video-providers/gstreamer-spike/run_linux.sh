#!/usr/bin/env bash
# Run the GStreamer spike on Linux (WSL2, Raspberry Pi OS, any desktop distro).
#
#   ./run_linux.sh TAG            e.g. ./run_linux.sh pi5   or   ./run_linux.sh wsl
#
# Needs distro GStreamer + gi (see GSTREAMER-SPIKE.md, "Linux and Raspberry Pi
# packages"). Creates ~/gst-spike-venv with --system-site-packages so the distro
# python3-gi is importable, installs Kivy (KIVY_SPEC, default kivy==2.3.2: the
# newest release with manylinux x86_64 + aarch64 wheels), then writes
# results/*-TAG.json and results/shot-TAG-*.png.
#
# Env: KIVY_SPEC, SKIP_HEADLESS=1, SKIP_KIVY=1, GSTSPIKE_CLIPS (comma list).
set -u
TAG=${1:?usage: run_linux.sh TAG}
cd "$(dirname "$0")"
mkdir -p results
LOG=results/run-$TAG.log
exec > >(tee "$LOG") 2>&1

echo "== host"
uname -a; head -3 /etc/os-release; python3 --version
cat /proc/device-tree/model 2>/dev/null; echo
echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-}"
echo "== gstreamer"
gst-inspect-1.0 --version | head -2
for e in playbin3 appsink glupload glcolorconvert hlsdemux2 dashdemux2 tsdemux \
         avdec_h264 avdec_h265 v4l2h264dec v4l2slh264dec v4l2slh265dec v4l2h265dec \
         vah264dec vah265dec d3d12h264dec; do
  if gst-inspect-1.0 --exists "$e" 2>/dev/null; then echo "  $e: yes"; else echo "  $e: -"; fi
done
gst-inspect-1.0 opengl 2>/dev/null | grep -iE "platform|api" | head -3
ls /dev/video* /dev/dri/* 2>/dev/null | tr '\n' ' '; echo
command -v vainfo >/dev/null && vainfo 2>&1 | grep -E "Driver version|VAProfileH264|VAProfileHEVC" | head -6

VENV=$HOME/gst-spike-venv
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv --system-site-packages "$VENV" || exit 1
fi
PY=$VENV/bin/python
$PY -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None); print('gi', gi.__version__, Gst.version_string())" || {
  echo "gi/Gst not importable: install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0"; exit 1; }

if [ -z "${SKIP_HEADLESS:-}" ]; then
  echo "== clips"
  $PY make_clips.py 2>&1 | grep -v Pango
  echo "== headless (playbin3)"
  $PY gst_headless.py --all --bin playbin3 --json "results/headless-$TAG.json" 2>&1 | grep -v Pango
  echo "== no-gi (ctypes)"
  $PY gst_nogi.py clips/test_1080p30.mp4 6
  $PY gst_nogi.py clips/hevc_1080p30.mp4 6
fi


kivy_run() {  # kivy_run SUFFIX [ENV=VAL ...]
  local suffix=$1; shift
  env "$@" GSTSPIKE_TAG="$TAG$suffix" timeout 400 "$PY" main.py --auto \
    "results/kivy-$TAG$suffix.json" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
    | grep -E "OpenGL version|RESULT|Traceback|Error" | cut -c1-500
}

if [ -z "${SKIP_KIVY:-}" ]; then
  echo "== kivy"
  $PY -m pip install -q "${KIVY_SPEC:-kivy==2.3.2}" || exit 1
  export GSTSPIKE_CLIPS=${GSTSPIKE_CLIPS:-big_buck_bunny_360_10s.mp4,test_1080p30.mp4,hevc_1080p30.mp4,hevc_2160p30.mp4}
  unset KIVY_NO_CONSOLELOG  # Kivy mutes the console if it is set at all
  echo "-- default GL context (SDL2 on X11 = GLX)"
  kivy_run "" SDL_VIDEO_X11_FORCE_EGL=0
  echo "-- SDL on X11 forced to EGL (DMABuf import needs EGL)"
  kivy_run -egl SDL_VIDEO_X11_FORCE_EGL=1
  echo "-- EGL gl mode without releasing Kivy's context (open question 2)"
  kivy_run -norelease SDL_VIDEO_X11_FORCE_EGL=1 GSTSPIKE_GL_RELEASE=0 \
    GSTSPIKE_MODES=gl GSTSPIKE_CLIPS=hevc_1080p30.mp4
fi
echo "== done: results/*$TAG*"
