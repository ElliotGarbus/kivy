"""Make the test media in src/clips/ from the spike's H.264 clip.

- clip.mp4: Big Buck Bunny, 640x360 H.264/AAC, 10 s. 360 isn't a multiple of
  16, so the decoder outputs 368 rows and the texture transform has to crop.
- clip_rot90.mp4: the same file with its video track's display matrix set to
  a 90 degree rotation, the way a phone records portrait video.
- corrupt.mp4: random bytes.
"""

import random
import shutil
import struct
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE.parent / "android-exoplayer-spike" / "src" / "clips" / "clip.mp4"
OUT = HERE / "src" / "clips"
CONTAINERS = {b"moov", b"trak", b"mdia"}
ROTATE_90 = struct.pack(
    ">9i", 0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000)


def boxes(data, start, end):
    while start < end:
        size, kind = struct.unpack(">I4s", data[start:start + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[start + 8:start + 16])[0]
            header = 16
        yield kind, start, start + header, start + size
        start += size


def video_tkhd_offsets(data):
    found = []

    def walk(start, end, track):
        for kind, _, body, box_end in boxes(data, start, end):
            if kind == b"trak":
                track = {"tkhd": None, "video": False}
                walk(body, box_end, track)
                if track["video"]:
                    found.append(track["tkhd"])
            elif kind in CONTAINERS:
                walk(body, box_end, track)
            elif kind == b"tkhd":
                track["tkhd"] = body
            elif kind == b"hdlr" and data[body + 8:body + 12] == b"vide":
                track["video"] = True

    walk(0, len(data), None)
    return found


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, OUT / "clip.mp4")

    data = bytearray(SOURCE.read_bytes())
    offsets = video_tkhd_offsets(data)
    assert len(offsets) == 1, offsets
    tkhd = offsets[0]
    matrix_at = tkhd + (52 if data[tkhd] == 1 else 40)
    data[matrix_at:matrix_at + 36] = ROTATE_90
    (OUT / "clip_rot90.mp4").write_bytes(data)

    rng = random.Random(9528)
    (OUT / "corrupt.mp4").write_bytes(bytes(rng.randrange(256) for _ in range(65536)))
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
