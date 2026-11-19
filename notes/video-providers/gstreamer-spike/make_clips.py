"""Generate the spike's local test clips with GStreamer itself.

    python make_clips.py [--force]  -> clips/test_{1080,2160}p30.mp4 (H.264),
                                       clips/hevc_{1080,2160}p30.mp4 (HEVC),
                                       clips/vp9_1080p.webm, corrupt.mp4, not_a_video.mp4

Existing clips are kept unless --force. Encoders are picked from whatever is
installed (openh264enc/x264enc, nvh265enc/mfh265enc/qsvh265enc/x265enc), so
it runs from the official Windows wheels or from distro packages. The Pi 5
only decodes HEVC in hardware, hence the HEVC clips; on a Pi without an HEVC
encoder, copy them over from a machine that has one.
"""
import os
import sys

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, 'clips')


def run(desc):
    print('->', desc)
    p = Gst.parse_launch(desc)
    p.set_state(Gst.State.PLAYING)
    msg = p.get_bus().timed_pop_filtered(
        Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    p.set_state(Gst.State.NULL)
    if msg.type == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        sys.exit(f'failed: {err.message}\n{dbg}')


FORCE = '--force' in sys.argv

ENCODERS = {
    # name -> property string; bitrate placeholders filled per clip
    'h264': [('openh264enc', 'bitrate={bps}'), ('x264enc', 'bitrate={kbps} speed-preset=ultrafast'),
             ('nvh264enc', 'bitrate={kbps}'), ('mfh264enc', 'bitrate={kbps}')],
    'h265': [('nvh265enc', 'bitrate={kbps}'), ('qsvh265enc', 'bitrate={kbps}'),
             ('mfh265enc', 'bitrate={kbps}'), ('x265enc', 'bitrate={kbps} speed-preset=ultrafast')],
}


def encoder(kind, bps):
    for name, props in ENCODERS[kind]:
        if Gst.ElementFactory.find(name):
            return f'{name} ' + props.format(bps=bps, kbps=bps // 1000)
    return None


def aac():
    for name in ('avenc_aac', 'fdkaacenc', 'voaacenc', 'faac'):
        if Gst.ElementFactory.find(name):
            return name
    return None


def clip(name, kind, w, h, secs, bitrate, pattern='ball'):
    out = os.path.join(CLIPS, name).replace(os.sep, '/')
    if os.path.exists(out) and not FORCE:
        print('keep', name)
        return out
    enc = encoder(kind, bitrate)
    if enc is None:
        print(f'skip {name}: no {kind} encoder installed')
        return None
    parse = 'h264parse' if kind == 'h264' else 'h265parse'
    n = secs * 30
    audio = ''
    if aac():
        audio = (f'audiotestsrc num-buffers={secs * 43} samplesperbuffer=1024 ! '
                 f'audio/x-raw,rate=44100 ! audioconvert ! {aac()} ! aacparse ! queue ! mux. ')
    run(f'videotestsrc num-buffers={n} pattern={pattern} animation-mode=frames ! '
        f'video/x-raw,width={w},height={h},framerate=30/1 ! timeoverlay font-desc="Sans 48" ! '
        f'videoconvert ! video/x-raw,format=NV12 ! {enc} ! {parse} ! queue ! mux. '
        f'{audio}mp4mux name=mux ! filesink location="{out}"')
    return out


def h264(name, w, h, secs, bitrate):
    return clip(name, 'h264', w, h, secs, bitrate)


def main():
    Gst.init(None)
    os.makedirs(CLIPS, exist_ok=True)
    h264('test_1080p30.mp4', 1920, 1080, 20, 8_000_000)
    big = h264('test_2160p30.mp4', 3840, 2160, 10, 20_000_000)
    clip('hevc_1080p30.mp4', 'h265', 1920, 1080, 20, 6_000_000, pattern='smpte')
    clip('hevc_2160p30.mp4', 'h265', 3840, 2160, 10, 16_000_000)
    out = os.path.join(CLIPS, 'vp9_1080p.webm').replace(os.sep, '/')
    if not os.path.exists(out) or FORCE:
        run(f'videotestsrc num-buffers=150 pattern=smpte ! video/x-raw,width=1920,height=1080,framerate=30/1 ! '
            f'videoconvert ! vp9enc deadline=1 cpu-used=8 ! webmmux ! filesink location="{out}"')
    # A file that has a valid MP4 header but garbage after the first 64 KiB.
    if big and (FORCE or not os.path.exists(os.path.join(CLIPS, 'corrupt.mp4'))):
        with open(big, 'rb') as f:
            head = f.read(65536)
        with open(os.path.join(CLIPS, 'corrupt.mp4'), 'wb') as f:
            f.write(head + os.urandom(2_000_000))
    with open(os.path.join(CLIPS, 'not_a_video.mp4'), 'wb') as f:
        f.write(b'this is plain text, not a video\n' * 100)
    print('done')


if __name__ == '__main__':
    main()
