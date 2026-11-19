#!/usr/bin/env bash
# Kivy 3.0 (SDL3) matrix on Linux: GL backend (gl, sdl3) x SDL video setup
# (native Wayland, X11 + GLX, X11 + forced EGL).
#   ./run_kivy3_matrix.sh TAG KIVY_SRC VENV
# KIVY_SRC is the tree build_kivy3_linux.sh built (for its SDL3 libs).
set -u
TAG=${1:?TAG}; SRC=$(realpath "${2:?KIVY_SRC}"); VENV=${3:?VENV}
cd "$(dirname "$0")"
mkdir -p results
export LD_LIBRARY_PATH="$SRC/kivy-dependencies/dist/lib:$SRC/kivy-dependencies/dist/lib64"
export GSTSPIKE_CLIPS=${GSTSPIKE_CLIPS:-test_1080p30.mp4,hevc_1080p30.mp4,hevc_2160p30.mp4}
unset KIVY_NO_CONSOLELOG
PY=$VENV/bin/python
for cgl in gl sdl3; do
  for disp in wayland x11-glx x11-egl; do
    case $disp in
      wayland) envs="SDL_VIDEO_DRIVER=wayland SDL_VIDEODRIVER=wayland" ;;
      x11-glx) envs="SDL_VIDEO_DRIVER=x11 SDL_VIDEODRIVER=x11 SDL_VIDEO_FORCE_EGL=0" ;;
      x11-egl) envs="SDL_VIDEO_DRIVER=x11 SDL_VIDEODRIVER=x11 SDL_VIDEO_FORCE_EGL=1" ;;
    esac
    name=$TAG-k3-$cgl-$disp
    echo "== $name"
    env $envs KIVY_GL_BACKEND=$cgl GSTSPIKE_TAG=$name timeout 400 \
      "$PY" main.py --auto "results/kivy-$name.json" 2>&1 \
      | sed 's/\x1b\[[0-9;]*m//g' \
      | grep -E "OpenGL version|GL: Backend|Window  .*Provider|RESULT|Traceback|Error" \
      | cut -c1-460
    "$PY" - "results/kivy-$name.json" <<'PYEOF' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
m = d['meta']
print('   meta:', m.get('kivy'), m.get('cgl_backend'), m.get('sdl_video_driver'), m.get('gl_platform'), m.get('gl'))
PYEOF
  done
done
echo "== done"
