#!/usr/bin/env bash
# Runs INSIDE the container: rebuild the extension, then (1) narrow the Ubuntu
# 24.04 crash, (2) run the window-free tests with kivy.tests.common stubbed.
set -u
export DEBIAN_FRONTEND=noninteractive
TAG=$1
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-dev python3-venv gcc ca-certificates \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  gstreamer1.0-gl gstreamer1.0-tools > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
python3 -m venv /venv
/venv/bin/pip install -q "cython==3.2.0" pytest pytest-timeout > /tmp/pip.log 2>&1
cp -r /src /kivy
cd /kivy/kivy/lib/gstplayer
/venv/bin/cython -3 --module-name kivy.lib.gstplayer._gstplayer _gstplayer.pyx -o _gstplayer.c
EXT=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
INC=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['include'])")
gcc -shared -fPIC -O2 -Wno-unused-function -I"$INC" _gstplayer.c -o "_gstplayer$EXT" -ldl
export PYTHONPATH=/kivy KIVY_NO_ARGS=1 KIVY_HOME=/tmp/kivyhome
cd /spike
if [ "$TAG" = ubuntu2404 ]; then
  echo "== which audio sink autoaudiosink picks, and the crash"
  gst-inspect-1.0 openalsink > /dev/null 2>&1 && echo "openalsink present" || echo "no openalsink"
  gst-launch-1.0 -q filesrc location=clips/test_1080p30.mp4 ! qtdemux ! fakesink > /dev/null 2>&1; echo "(no-audio clip check done)"
  for rank in "" "openalsink:NONE"; do
    echo "-- GST_PLUGIN_FEATURE_RANK='$rank'"
    GST_PLUGIN_FEATURE_RANK=$rank /venv/bin/python -u core_headless.py --case local-1080p --case local-hevc-2160p --case hls-ts 2>&1 \
      | grep -E "^local|^hls|ERROR|Segmentation|Abort|core dumped" | cut -c1-150
    echo "   exit ${PIPESTATUS[0]}"
  done
  echo "-- gst-launch playbin3 with autoaudiosink, 3 s"
  timeout 10 gst-launch-1.0 -q playbin3 uri=file:///spike/clips/test_1080p30.mp4 video-sink=fakesink 2>&1 | grep -viE "^\[W|^\[E|ALSOFT" | tail -3
  echo "   exit ${PIPESTATUS[0]}"
  echo "== headless cases with openalsink ranked out"
  GST_PLUGIN_FEATURE_RANK=openalsink:NONE /venv/bin/python -u core_headless.py --all --fresh-registry --json /out/core-$TAG.json 2>&1 \
    | grep -vE "^\[(INFO|WARNING)|buffering:|decoder:|pw.conf|ALSOFT|ALSA|AL lib" | cut -c1-170
fi
echo "== window-free tests ($TAG)"
cat > /kivy/kivy/tests/common.py <<'PY'
# Stub for a Kivy build without compiled graphics: only the window-free tests run.
import unittest
GraphicUnitTest = unittest.TestCase
PY
cd /tmp
GST_PLUGIN_FEATURE_RANK=openalsink:NONE /venv/bin/python -m pytest -q -p no:cacheprovider --noconftest --timeout 120 \
  /kivy/kivy/tests/test_gstplayer_policy.py "/kivy/kivy/tests/test_video_gstplayer.py::GstPlayerCoreTestCase" 2>&1 \
  | grep -vE "pw.conf|ALSOFT|ALSA|AL lib" | tail -6
