#!/usr/bin/env bash
# Runs INSIDE the container: only the window-free tests, showing collection errors.
set -u
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-dev python3-venv gcc ca-certificates \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  gstreamer1.0-gl > /tmp/apt.log 2>&1
python3 -m venv /venv
/venv/bin/pip install -q "cython==3.2.0" pytest pytest-timeout > /tmp/pip.log 2>&1
cp -r /src /kivy
cd /kivy/kivy/lib/gstplayer
/venv/bin/cython -3 --module-name kivy.lib.gstplayer._gstplayer _gstplayer.pyx -o _gstplayer.c
EXT=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
INC=$(/venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['include'])")
gcc -shared -fPIC -O2 -Wno-unused-function -I"$INC" _gstplayer.c -o "_gstplayer$EXT" -ldl
export PYTHONPATH=/kivy KIVY_NO_ARGS=1 KIVY_HOME=/tmp/kivyhome GST_PLUGIN_FEATURE_RANK=openalsink:NONE
mkdir -p /t && cp /kivy/kivy/tests/test_gstplayer_policy.py /kivy/kivy/tests/test_video_gstplayer.py /t/
cat > /t/conftest.py <<'PY'
# Kivy is built here without its compiled graphics, so kivy.tests (whose
# __init__ imports the graphics test helpers) is replaced by stand-ins:
# only the window-free tests can run.
import sys, types, unittest
pkg = types.ModuleType('kivy.tests'); pkg.__path__ = []
common = types.ModuleType('kivy.tests.common'); common.GraphicUnitTest = unittest.TestCase
sys.modules['kivy.tests'] = pkg; sys.modules['kivy.tests.common'] = common
PY
cd /t
/venv/bin/python -m pytest -q -p no:cacheprovider --timeout 120 test_gstplayer_policy.py \
  "test_video_gstplayer.py::GstPlayerCoreTestCase" 2>&1 | grep -vE "pw.conf|ALSOFT|ALSA|AL lib|^\[INFO" | tail -8