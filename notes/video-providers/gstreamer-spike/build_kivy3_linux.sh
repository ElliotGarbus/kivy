#!/usr/bin/env bash
# Build Kivy 3.0 from a source tree on Debian / Ubuntu / Raspberry Pi OS,
# against Kivy's own SDL3 dependency build (tools/build_linux_dependencies.sh),
# into a --system-site-packages venv (so distro python3-gi stays importable).
#
#   ./build_kivy3_linux.sh SRC_DIR VENV_DIR
#
# Run the result with:
#   LD_LIBRARY_PATH=SRC_DIR/kivy-dependencies/dist/lib:SRC_DIR/kivy-dependencies/dist/lib64 VENV_DIR/bin/python ...
set -eu
SRC=$(realpath "$1")
VENV=$2

# The package list from doc/sources/installation/installation-linux.rst,
# plus wayland-protocols / libdecor for SDL3's Wayland backend.
PKGS="python3-dev python3-venv build-essential git make autoconf automake libtool
pkg-config cmake meson ninja-build curl libasound2-dev libpulse-dev libaudio-dev
libjack-dev libsndio-dev libsamplerate0-dev libx11-dev libxext-dev libxrandr-dev
libxcursor-dev libxfixes-dev libxi-dev libxss-dev libxtst-dev libxinerama-dev
libwayland-dev wayland-protocols libdecor-0-dev libxkbcommon-dev libdrm-dev
libgbm-dev libgl1-mesa-dev libgles2-mesa-dev libegl1-mesa-dev libdbus-1-dev
libibus-1.0-dev libudev-dev fcitx-libs-dev libpipewire-0.3-dev libmtdev-dev"
avail=""
missing=""
for p in $PKGS; do
  if apt-cache show "$p" >/dev/null 2>&1; then avail="$avail $p"; else missing="$missing $p"; fi
done
echo "== apt: not available here:${missing:- none}"
need=""
for p in $avail; do
  dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"
done
if [ -n "$need" ]; then
  echo "== apt: installing:$need"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need
fi

cd "$SRC"
if ! ls kivy-dependencies/dist/lib*/libSDL3.so* >/dev/null 2>&1; then
  echo "== building SDL3 dependencies ($(date +%T))"
  rm -rf kivy-dependencies
  bash tools/build_linux_dependencies.sh > deps-build.log 2>&1 || {
    tail -40 deps-build.log; exit 1; }
  echo "== dependencies done ($(date +%T))"
fi
ls kivy-dependencies/dist/lib*/ | grep -E "SDL3|thorvg" | head

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install -q -U pip "setuptools~=82.0.0" "cython>=0.29.1,<=3.2.0" wheel
echo "== building kivy ($(date +%T))"
KIVY_DEPS_ROOT="$SRC/kivy-dependencies" USE_X11=1 KIVY_SPLIT_EXAMPLES=1 \
  "$VENV/bin/pip" install --no-build-isolation "$SRC" > kivy-build.log 2>&1 || {
    tail -40 kivy-build.log; exit 1; }
echo "== kivy done ($(date +%T))"
cd /
LD_LIBRARY_PATH="$SRC/kivy-dependencies/dist/lib:$SRC/kivy-dependencies/dist/lib64" \
  "$VENV/bin/python" -c "import kivy, kivy.core.video as v; print('kivy', kivy.__version__, 'on_error' in v.VideoBase.__events__)"
