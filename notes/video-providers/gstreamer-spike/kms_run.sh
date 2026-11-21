#!/usr/bin/env bash
# Spike 5b: the provider check under SDL3's KMSDRM backend (no desktop).
# Run on the Pi with the desktop stopped (lightdm), e.g.:
#   sudo systemctl stop lightdm; bash ~/gstreamer-spike/kms_run.sh; sudo systemctl start lightdm
cd ~/gstreamer-spike || exit 1
mkdir -p results/pi5-spike5
unset DISPLAY WAYLAND_DISPLAY
export SDL_VIDEO_DRIVER=kmsdrm
export LD_LIBRARY_PATH=$HOME/kivy-src/kivy-dependencies/dist/lib:$HOME/kivy-src/kivy-dependencies/dist/lib64
export PCHECK_PATHS=auto,gl,nv12 PCHECK_PLAY=6
export PCHECK_CLIPS=hevc_2160p30.mp4,test_1080p30.mp4
timeout 300 ~/kivy3-gst-venv/bin/python provider_check.py results/pi5-spike5/kmsdrm.json \
  > results/pi5-spike5/kmsdrm.log 2>&1
echo "kmsdrm check exit $?  (results in ~/gstreamer-spike/results/pi5-spike5/)"
