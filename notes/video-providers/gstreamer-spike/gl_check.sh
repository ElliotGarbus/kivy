#!/usr/bin/env bash
# Quick GL-mode check on Linux: GLX vs EGL (SDL_VIDEO_X11_FORCE_EGL=1) vs
# native Wayland (SDL_VIDEODRIVER=wayland), one clip, gl mode only.
#   ./gl_check.sh [clip] [modes]
cd "$(dirname "$0")"
CLIP=${1:-hevc_1080p30.mp4}
MODES=${2:-gl}
PY=$HOME/gst-spike-venv/bin/python
unset KIVY_NO_CONSOLELOG
for variant in glx egl wayland; do
  echo "== $variant"
  case $variant in
    glx) envs="SDL_VIDEO_X11_FORCE_EGL=0" ;;
    egl) envs="SDL_VIDEO_X11_FORCE_EGL=1" ;;
    wayland) envs="SDL_VIDEODRIVER=wayland" ;;
  esac
  env $envs GSTSPIKE_TAG=${TAG:-check}-$variant GSTSPIKE_CLIPS=$CLIP GSTSPIKE_MODES=$MODES \
    timeout 60 $PY main.py --auto "results/glcheck-${TAG:-check}-$variant.json" 2>&1 \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | grep -vE "xinput|^\s*$" \
    | grep -E -A12 "OpenGL version|Window  .*Provider|RESULT.*$CLIP|decoder=|Traceback" \
    | grep -vE "^\[INFO   \] \[(GL|Window|Text|Factory|Image|Kivy|Python|Logger|Base|MTD|ProbeSysfs|deps)" \
    | cut -c1-500 | head -40
done
