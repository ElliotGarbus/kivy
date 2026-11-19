"""Copy the Kivy wheels and the two bundled clips from android-provider-check.

Run that app's make_clips.py and make_wheels.py first. make_wheels.py overlays
the Python of whatever branch the Kivy checkout has, so run it against a
checkout of android-exoplayer-video (the Media3 provider), not master.

    python prepare.py
    kivyforge lock -p android
    kivyforge run -p android --device
"""

import shutil
from pathlib import Path

HERE = Path(__file__).parent
CHECK = HERE.parent / "android-provider-check"


def main():
    (HERE / "wheels").mkdir(exist_ok=True)
    for wheel in sorted((CHECK / "wheels").glob("kivy-*.whl")):
        shutil.copy2(wheel, HERE / "wheels" / wheel.name)
        print("wheel", wheel.name)
    (HERE / "src" / "clips").mkdir(exist_ok=True)
    for name in ("clip.mp4", "clip_rot90.mp4"):
        shutil.copy2(CHECK / "src" / "clips" / name, HERE / "src" / "clips" / name)
        print("clip", name)


if __name__ == "__main__":
    main()
