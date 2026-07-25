import argparse
import os
import re
import zipfile

KIVY_DEPS_ROOT = os.environ.get("KIVY_DEPS_ROOT")
if not KIVY_DEPS_ROOT:
    raise EnvironmentError(
        "KIVY_DEPS_ROOT environment variable is not set. "
        "Please set it to the path where Android SDL3 deps are located."
    )

_ABI_FROM_WHEEL = (
    (re.compile(r"android_\d+_arm64_v8a"), "arm64-v8a"),
    (re.compile(r"android_\d+_x86_64"), "x86_64"),
)

_SDL_LIBS = (
    "libSDL3.so",
    "libSDL3_image.so",
    "libSDL3_mixer.so",
    "libSDL3_ttf.so",
)


def _abi_for_wheel(wheel_name: str) -> str:
    for pattern, abi in _ABI_FROM_WHEEL:
        if pattern.search(wheel_name):
            return abi
    raise ValueError(f"Could not determine Android ABI from wheel name: {wheel_name}")


def add_android_libs_to_wheels(wheels_path: str) -> None:
    libs_root = os.path.join(KIVY_DEPS_ROOT, "dist", "libs")
    if not os.path.isdir(libs_root):
        raise FileNotFoundError(f"Android libs folder missing at: {libs_root}")

    if not os.path.isdir(wheels_path):
        raise FileNotFoundError(f"Wheels folder missing at: {wheels_path}")

    for wheel in sorted(os.listdir(wheels_path)):
        if not wheel.endswith(".whl"):
            continue
        abi = _abi_for_wheel(wheel)
        abi_libs = os.path.join(libs_root, abi)
        wheel_path = os.path.join(wheels_path, wheel)
        print(f"Adding Android SDL3 libs ({abi}) to {wheel_path}")
        with zipfile.ZipFile(
            wheel_path,
            "a",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as whl:
            existing = set(whl.namelist())
            for lib_name in _SDL_LIBS:
                src = os.path.join(abi_libs, lib_name)
                if not os.path.isfile(src):
                    raise FileNotFoundError(f"Missing {src}")
                arcname = f".libs/{lib_name}"
                if arcname in existing:
                    print(f"  skip existing {arcname}")
                    continue
                print(f"  add {arcname}")
                whl.write(src, arcname)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Add Android SDL3 shared libraries into .libs/ of each wheel, "
            "keeping sonames unchanged (no auditwheel rename)."
        )
    )
    parser.add_argument(
        "wheels_path",
        help="Directory containing Android wheels to patch.",
    )
    args = parser.parse_args()
    add_android_libs_to_wheels(args.wheels_path)