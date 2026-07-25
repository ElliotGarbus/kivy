#!/bin/bash
# Download official SDL3 Android devel packages and arrange them into the
# layout setup.py expects when KIVY_CROSS_PLATFORM=android:
#
#   android-kivy-dependencies/dist/libs/{arm64-v8a,x86_64}/libSDL3*.so
#   android-kivy-dependencies/dist/include/{SDL3,SDL3_image,SDL3_mixer,SDL3_ttf}/
#
# Official assets ship as AARs with Android Prefab modules; we unpack those
# rather than compiling from source. Versions align with
# tools/build_ios_dependencies.sh where possible.
set -e -x

_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
_DEPS_ROOT="${_REPO_ROOT}/android-kivy-dependencies"

# Android SDL3 (official prebuilt)
ANDROID__SDL3__VERSION="3.4.2"
ANDROID__SDL3__URL="https://github.com/libsdl-org/SDL/releases/download/release-${ANDROID__SDL3__VERSION}/SDL3-devel-${ANDROID__SDL3__VERSION}-android.zip"
ANDROID__SDL3__AAR="SDL3-${ANDROID__SDL3__VERSION}.aar"
ANDROID__SDL3__HEADERS_MODULE="SDL3-Headers"
ANDROID__SDL3__SHARED_MODULE="SDL3-shared"
ANDROID__SDL3__LIB="libSDL3.so"
ANDROID__SDL3__INCLUDE_NAME="SDL3"

# Android SDL3_image
ANDROID__SDL3_IMAGE__VERSION="3.4.0"
ANDROID__SDL3_IMAGE__URL="https://github.com/libsdl-org/SDL_image/releases/download/release-${ANDROID__SDL3_IMAGE__VERSION}/SDL3_image-devel-${ANDROID__SDL3_IMAGE__VERSION}-android.zip"
ANDROID__SDL3_IMAGE__AAR="SDL3_image-${ANDROID__SDL3_IMAGE__VERSION}.aar"
ANDROID__SDL3_IMAGE__HEADERS_MODULE="SDL3_image-shared"
ANDROID__SDL3_IMAGE__SHARED_MODULE="SDL3_image-shared"
ANDROID__SDL3_IMAGE__LIB="libSDL3_image.so"
ANDROID__SDL3_IMAGE__INCLUDE_NAME="SDL3_image"

# Android SDL3_mixer
ANDROID__SDL3_MIXER__VERSION="3.2.0"
ANDROID__SDL3_MIXER__URL="https://github.com/libsdl-org/SDL_mixer/releases/download/release-${ANDROID__SDL3_MIXER__VERSION}/SDL3_mixer-devel-${ANDROID__SDL3_MIXER__VERSION}-android.zip"
ANDROID__SDL3_MIXER__AAR="SDL3_mixer-${ANDROID__SDL3_MIXER__VERSION}.aar"
ANDROID__SDL3_MIXER__HEADERS_MODULE="SDL3_mixer-shared"
ANDROID__SDL3_MIXER__SHARED_MODULE="SDL3_mixer-shared"
ANDROID__SDL3_MIXER__LIB="libSDL3_mixer.so"
ANDROID__SDL3_MIXER__INCLUDE_NAME="SDL3_mixer"

# Android SDL3_ttf
ANDROID__SDL3_TTF__VERSION="3.2.2"
ANDROID__SDL3_TTF__URL="https://github.com/libsdl-org/SDL_ttf/releases/download/release-${ANDROID__SDL3_TTF__VERSION}/SDL3_ttf-devel-${ANDROID__SDL3_TTF__VERSION}-android.zip"
ANDROID__SDL3_TTF__AAR="SDL3_ttf-${ANDROID__SDL3_TTF__VERSION}.aar"
ANDROID__SDL3_TTF__HEADERS_MODULE="SDL3_ttf-shared"
ANDROID__SDL3_TTF__SHARED_MODULE="SDL3_ttf-shared"
ANDROID__SDL3_TTF__LIB="libSDL3_ttf.so"
ANDROID__SDL3_TTF__INCLUDE_NAME="SDL3_ttf"

# ABIs produced for kivyforge / cibuildwheel Android wheels
ANDROID__ABIS=("arm64-v8a" "x86_64")

rm -rf "${_DEPS_ROOT}"
mkdir -p "${_DEPS_ROOT}/download" "${_DEPS_ROOT}/aar" "${_DEPS_ROOT}/dist/include"
for abi in "${ANDROID__ABIS[@]}"; do
  mkdir -p "${_DEPS_ROOT}/dist/libs/${abi}"
done

echo "Downloading official SDL3 Android devel packages..."
pushd "${_DEPS_ROOT}/download"
curl -L "${ANDROID__SDL3__URL}" -o "SDL3-devel-${ANDROID__SDL3__VERSION}-android.zip"
curl -L "${ANDROID__SDL3_IMAGE__URL}" -o "SDL3_image-devel-${ANDROID__SDL3_IMAGE__VERSION}-android.zip"
curl -L "${ANDROID__SDL3_MIXER__URL}" -o "SDL3_mixer-devel-${ANDROID__SDL3_MIXER__VERSION}-android.zip"
curl -L "${ANDROID__SDL3_TTF__URL}" -o "SDL3_ttf-devel-${ANDROID__SDL3_TTF__VERSION}-android.zip"
popd

extract_aar() {
  local zip_name="$1"
  local aar_name="$2"
  local dest_name="$3"
  local extract_root="${_DEPS_ROOT}/aar/${dest_name}"

  rm -rf "${extract_root}"
  mkdir -p "${extract_root}/zip" "${extract_root}/contents"
  unzip -q "${_DEPS_ROOT}/download/${zip_name}" -d "${extract_root}/zip"
  # AAR is a zip; unzip it into contents/
  unzip -q "${extract_root}/zip/${aar_name}" -d "${extract_root}/contents"
}

echo "Extracting AARs..."
extract_aar \
  "SDL3-devel-${ANDROID__SDL3__VERSION}-android.zip" \
  "${ANDROID__SDL3__AAR}" \
  "SDL3"
extract_aar \
  "SDL3_image-devel-${ANDROID__SDL3_IMAGE__VERSION}-android.zip" \
  "${ANDROID__SDL3_IMAGE__AAR}" \
  "SDL3_image"
extract_aar \
  "SDL3_mixer-devel-${ANDROID__SDL3_MIXER__VERSION}-android.zip" \
  "${ANDROID__SDL3_MIXER__AAR}" \
  "SDL3_mixer"
extract_aar \
  "SDL3_ttf-devel-${ANDROID__SDL3_TTF__VERSION}-android.zip" \
  "${ANDROID__SDL3_TTF__AAR}" \
  "SDL3_ttf"

install_component() {
  local dest_name="$1"
  local headers_module="$2"
  local shared_module="$3"
  local lib_name="$4"
  local include_name="$5"

  local prefab="${_DEPS_ROOT}/aar/${dest_name}/contents/prefab/modules"
  local headers_src="${prefab}/${headers_module}/include/${include_name}"
  local include_dst="${_DEPS_ROOT}/dist/include/${include_name}"

  if [ ! -d "${headers_src}" ]; then
    echo "ERROR: missing prefab headers at ${headers_src}" >&2
    exit 1
  fi

  rm -rf "${include_dst}"
  mkdir -p "${include_dst}"
  cp -a "${headers_src}/." "${include_dst}/"

  for abi in "${ANDROID__ABIS[@]}"; do
    local lib_src="${prefab}/${shared_module}/libs/android.${abi}/${lib_name}"
    if [ ! -f "${lib_src}" ]; then
      echo "ERROR: missing ${lib_name} for ABI ${abi} at ${lib_src}" >&2
      exit 1
    fi
    cp -a "${lib_src}" "${_DEPS_ROOT}/dist/libs/${abi}/${lib_name}"
  done
}

echo "Installing into dist/ layout..."
install_component \
  "SDL3" \
  "${ANDROID__SDL3__HEADERS_MODULE}" \
  "${ANDROID__SDL3__SHARED_MODULE}" \
  "${ANDROID__SDL3__LIB}" \
  "${ANDROID__SDL3__INCLUDE_NAME}"
install_component \
  "SDL3_image" \
  "${ANDROID__SDL3_IMAGE__HEADERS_MODULE}" \
  "${ANDROID__SDL3_IMAGE__SHARED_MODULE}" \
  "${ANDROID__SDL3_IMAGE__LIB}" \
  "${ANDROID__SDL3_IMAGE__INCLUDE_NAME}"
install_component \
  "SDL3_mixer" \
  "${ANDROID__SDL3_MIXER__HEADERS_MODULE}" \
  "${ANDROID__SDL3_MIXER__SHARED_MODULE}" \
  "${ANDROID__SDL3_MIXER__LIB}" \
  "${ANDROID__SDL3_MIXER__INCLUDE_NAME}"
install_component \
  "SDL3_ttf" \
  "${ANDROID__SDL3_TTF__HEADERS_MODULE}" \
  "${ANDROID__SDL3_TTF__SHARED_MODULE}" \
  "${ANDROID__SDL3_TTF__LIB}" \
  "${ANDROID__SDL3_TTF__INCLUDE_NAME}"

echo "Android SDL3 dependencies ready under ${_DEPS_ROOT}/dist"
find "${_DEPS_ROOT}/dist" -type f | sort
