"""Estimate a pruned Windows GStreamer bundle from the official wheels.

Walks PE import tables (stdlib only) from an allowlist of plugins and the
core libs a provider loads, and sums the transitive DLL closure that lives
in the installed ``gstreamer_*`` wheels. This is what kivyforge would ship
if it pruned ``gstreamer-meta`` to a video-playback allowlist.

    python prune_estimate.py <site-packages>
"""
import glob
import os
import struct
import sys

PLUGINS = [
    # pipeline core
    'coreelements', 'playback', 'app', 'typefindfunctions', 'autodetect',
    # raw video/audio plumbing
    'videoconvertscale', 'audioconvert', 'audioresample', 'volume', 'videorate',
    # Windows audio out + GPU decode + GL hand-off
    'wasapi2', 'd3d11', 'd3d12', 'opengl',
    # containers / parsers
    'isomp4', 'matroska', 'videoparsersbad', 'audioparsers', 'id3demux', 'subparse',
    # software decoders (libav = FFmpeg: H.264/HEVC/AAC..., patent-encumbered)
    'libav', 'dav1d', 'vpx', 'opus', 'vorbis', 'ogg',
    # network + HLS/DASH
    'soup', 'adaptivedemux2',
]
CORE_LIBS = ['gstreamer-1.0-0.dll', 'gstapp-1.0-0.dll', 'gstvideo-1.0-0.dll',
             'gstgl-1.0-0.dll', 'gstpbutils-1.0-0.dll']


def pe_imports(path):
    with open(path, 'rb') as f:
        data = f.read()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    optsz = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    imp_rva = struct.unpack_from('<I', data, dd + 8)[0]
    secs = []
    for i in range(nsec):
        s = opt + optsz + i * 40
        vsize, va, rsize, raw = struct.unpack_from('<IIII', data, s + 8)
        secs.append((va, max(vsize, rsize), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        return None

    names = []
    o = off(imp_rva) if imp_rva else None
    while o is not None:
        name_rva = struct.unpack_from('<I', data, o + 12)[0]
        if not name_rva:
            break
        no = off(name_rva)
        names.append(data[no:data.index(b'\0', no)].decode().lower())
        o += 20
    return names


def main():
    sp = sys.argv[1]
    index = {}
    for f in glob.glob(os.path.join(sp, 'gstreamer_*', '**', '*.dll'), recursive=True):
        index.setdefault(os.path.basename(f).lower(), f)
    plugdir = os.path.join(sp, 'gstreamer_plugins', 'lib', 'gstreamer-1.0')
    todo = [os.path.join(plugdir, f'gst{p}.dll') for p in PLUGINS]
    todo = [p for p in todo if os.path.exists(p)]
    todo += [index[c] for c in CORE_LIBS if c in index]
    seen = set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for dep in pe_imports(f):
            if dep in index and index[dep] not in seen:
                todo.append(index[dep])
    total = sum(os.path.getsize(f) for f in seen)
    allsz = sum(os.path.getsize(f) for f in glob.glob(
        os.path.join(sp, 'gstreamer_*', '**', '*'), recursive=True) if os.path.isfile(f))
    print(f'allowlist: {len(PLUGINS)} plugins -> {len(seen)} DLLs, '
          f'{total / 1e6:.1f} MB (full install {allsz / 1e6:.1f} MB)')
    for s, f in sorted(((os.path.getsize(f), os.path.basename(f)) for f in seen),
                       reverse=True)[:10]:
        print(f'  {s / 1e6:6.1f} MB  {f}')
    libav = {'avcodec-61.dll', 'avformat-61.dll', 'avutil-59.dll', 'avfilter-10.dll',
             'swresample-5.dll', 'swscale-8.dll', 'gstlibav.dll'}
    no_libav = sum(os.path.getsize(f) for f in seen if os.path.basename(f) not in libav)
    print(f'without gst-libav (FFmpeg): {no_libav / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
