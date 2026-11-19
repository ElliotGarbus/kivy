"""Build Kivy 3 Android wheels carrying a Kivy branch's Python files.

There is no local cross-build for Kivy's Android wheels, so this takes the
newest published ones and overlays every ``kivy/**/*.py`` that differs in the
given checkout. That is only sound while the branch changes no compiled code
used on Android since the base wheels were built; check with
``git log --since=<build date> -- '*.pyx' '*.pxd' '*.pxi' '*.c' '*.h'``.

    python make_wheels.py PATH_TO_KIVY_CHECKOUT
"""

import base64
import csv
import hashlib
import io
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = (
    "https://github.com/ElliotGarbus/kivy-mobile-wheels/releases/download/"
    "kivy-android-3.0.0.dev202611051750/"
)
WHEELS = {
    "kivy-3.0.0.dev202611051750-cp314-cp314-android_24_arm64_v8a.whl":
        "eefe4143a180f7068899e97a0c19e34da1f55b4b2cdc31f6ecef7663a36f1c34",
    "kivy-3.0.0.dev202611051750-cp314-cp314-android_24_x86_64.whl":
        "39faf1b9a16c2704f363535ede34d1055fbc1356a918b775992536621bcdaac0",
}
HERE = Path(__file__).parent
# Written by the build itself; the base wheel's copies are the right ones.
GENERATED = {"kivy/_version.py", "kivy/setupconfig.py"}


def tracked_sources(checkout):
    """The checkout's committed ``kivy/**/*.py`` files, so build output in a
    checkout built in place (``setupconfig.py``, ...) is never overlaid."""
    out = subprocess.run(
        ["git", "-C", str(checkout), "ls-files", "kivy/*.py"],
        check=True, capture_output=True, text=True).stdout
    return sorted(out.split())


def fetch(name, sha256):
    # Not under wheels/: find_links would see two wheels with one name.
    cache = HERE / "base-wheels" / name
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(BASE + name) as response:
            cache.write_bytes(response.read())
    data = cache.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit("{}: hash mismatch".format(name))
    return data


def record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def overlay(data, checkout):
    source = zipfile.ZipFile(io.BytesIO(data))
    files = {info.filename: source.read(info) for info in source.infolist()}
    record_name = next(n for n in files if n.endswith(".dist-info/RECORD"))
    changed = []
    for rel in tracked_sources(checkout):
        if rel.startswith("kivy/tests/") or rel in GENERATED:
            continue
        # The checkout may use CRLF line endings; the wheels use LF.
        new = (checkout / rel).read_bytes().replace(b"\r\n", b"\n")
        if rel in files and files[rel] != new:
            changed.append(rel)
            files[rel] = new
        elif rel not in files and (Path(rel).parent.as_posix() + "/__init__.py") in files:
            changed.append(rel + " (new)")
            files[rel] = new

    rows = [
        (name, record_hash(content), str(len(content)))
        for name, content in files.items() if name != record_name
    ]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows + [(record_name, "", "")])
    files[record_name] = out.getvalue().encode()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(name, content)
    return buffer.getvalue(), changed


def main():
    checkout = Path(sys.argv[1]).resolve()
    for name, sha256 in WHEELS.items():
        patched, changed = overlay(fetch(name, sha256), checkout)
        (HERE / "wheels" / name).write_bytes(patched)
        print("{}: {} files overlaid".format(name, len(changed)))
    for rel in changed:
        print("  " + rel)


if __name__ == "__main__":
    main()
