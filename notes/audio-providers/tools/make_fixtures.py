"""Generate the audio fixtures for the Rubicon spike.

ffmpeg is not available on the spike machine, so this uses soundfile
(libsndfile >= 1.2 writes wav, aiff, flac, ogg/vorbis, ogg/opus and mp3) and
Apple's ``afconvert`` for the AAC containers (m4a, aac/adts) and caf.

Two signals are written in every format:

* ``tone10``: 10.0 s of a 440 Hz sine with a short click at every whole
  second, so drift and glitches are audible by ear.
* ``short1``: 1.0 s of a 440 Hz sine, used for end-of-playback and loop
  timing.

Writes ``fixtures/<signal>.<ext>`` and ``fixtures/manifest.json`` with the
true duration of each file as reported by ``afinfo`` (or soundfile when
CoreAudio cannot read the format).

Usage: python make_fixtures.py
"""

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 48000  # opus only supports 8/12/16/24/48 kHz
HERE = Path(__file__).parent
OUT = HERE / "fixtures"


def tone(seconds, clicks):
    t = np.arange(int(RATE * seconds)) / RATE
    y = 0.3 * np.sin(2 * np.pi * 440 * t)
    if clicks:
        click_len = int(0.005 * RATE)
        for s in range(int(seconds)):
            i = s * RATE
            y[i:i + click_len] = 0.9
    return y.astype(np.float32)


SF_FORMATS = {
    "wav": ("WAV", "PCM_16"),
    "aiff": ("AIFF", "PCM_16"),
    "flac": ("FLAC", "PCM_16"),
    "ogg": ("OGG", "VORBIS"),
    "opus": ("OGG", "OPUS"),
    "mp3": ("MP3", "MPEG_LAYER_III"),
}

AFCONVERT_FORMATS = {
    "m4a": ["-f", "m4af", "-d", "aac"],
    "aac": ["-f", "adts", "-d", "aac"],
    "caf": ["-f", "caff", "-d", "LEI16"],
}


def afinfo_duration(path):
    try:
        out = subprocess.run(
            ["afinfo", str(path)], capture_output=True, text=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        return None
    m = re.search(r"estimated duration:\s*([\d.]+)", out)
    return float(m.group(1)) if m else None


def sf_duration(path):
    try:
        info = sf.info(str(path))
    except Exception:
        return None
    return info.frames / info.samplerate


def main():
    OUT.mkdir(exist_ok=True)
    manifest = {}
    for name, seconds, clicks in (("tone10", 10.0, True),
                                  ("short1", 1.0, False)):
        y = tone(seconds, clicks)
        for ext, (fmt, subtype) in SF_FORMATS.items():
            path = OUT / f"{name}.{ext}"
            sf.write(str(path), y, RATE, format=fmt, subtype=subtype)
        wav = OUT / f"{name}.wav"
        for ext, args in AFCONVERT_FORMATS.items():
            path = OUT / f"{name}.{ext}"
            subprocess.run(["afconvert", *args, str(wav), str(path)],
                           check=True)
        for path in sorted(OUT.glob(f"{name}.*")):
            manifest[path.name] = {
                "nominal": seconds,
                "afinfo": afinfo_duration(path),
                "soundfile": sf_duration(path),
            }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for k, v in manifest.items():
        print(f"{k:14} {v}")


if __name__ == "__main__":
    main()
