#!/usr/bin/env bash
# Runs INSIDE the container (/src = Kivy branch, /spike = notes gstreamer-spike, /out = results).
set -u
export DEBIAN_FRONTEND=noninteractive
TAG=$1
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-dev python3-venv gcc ca-certificates \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  gstreamer1.0-gl gstreamer1.0-tools > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
echo "== $TAG: $(. /etc/os-release; echo $PRETTY_NAME), $(gst-launch-1.0 --version | sed -n 2p), $(python3 --version)"
python3 -m venv /venv
/venv/bin/pip install -q "cython==3.2.0" pytest pytest-timeout > /tmp/pip.log 2>&1 || { tail -20 /tmp/pip.log; exit 1; }
cp -r /src /kivy
cd /kivy/kivy/lib/gstplayer
/venv/bin/cython -3 --module-name kivy.lib.gstplayer._gstplayer _gstplayer.pyx -o _gstplayer.c
EXT=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
INC=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['include'])")
gcc -shared -fPIC -O2 -Wall -Wno-unused-function -I"$INC" _gstplayer.c -o "_gstplayer$EXT" -ldl 2>&1 | grep -E "warning|error" | head
export PYTHONPATH=/kivy KIVY_NO_ARGS=1 KIVY_HOME=/tmp/kivyhome
cd /spike
echo "== loader, GL entry points"
/venv/bin/python - <<'PY' 2>&1 | grep -v -E "^\[(INFO|WARNING)"
from kivy.lib import gstplayer as g
print('runtime', g.get_runtime_info())
print('MIN_VERSION', g.MIN_VERSION, 'has_gl (GL lib + 1.24 ensure_context)', g.has_gl())
print('init', g.init_wait(120), round(g.init_seconds(), 2))
for e in ('playbin3', 'appsink', 'glupload', 'hlsdemux2', 'dashdemux2', 'tsdemux', 'souphttpsrc', 'avdec_h264', 'avdec_h265', 'vp9dec'):
    print(' ', e, g.element_available(e))
PY
echo "== headless cases"
/venv/bin/python -u core_headless.py --all --fresh-registry --json /out/core-$TAG.json 2>&1 \
  | grep -vE "^\[(INFO|WARNING)|buffering:|decoder:" | cut -c1-170
echo "== core integration tests (no window) + policy tests"
cd /tmp
/venv/bin/python -m pytest -q -p no:cacheprovider --timeout 120 -rs \
  /kivy/kivy/tests/test_gstplayer_policy.py "/kivy/kivy/tests/test_video_gstplayer.py::GstPlayerCoreTestCase" 2>&1 | tail -6
