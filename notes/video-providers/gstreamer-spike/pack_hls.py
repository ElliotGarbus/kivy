import sys, os
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
clips = sys.argv[1]
for name in ('hlscmafsink', 'hlssink2', 'tsdemux', 'mpegtsmux', 'cmafmux', 'hlsdemux2', 'dashdemux2', 'qtdemux', 'souphttpsrc', 'vp8enc', 'webmmux', 'x264enc', 'openh264enc'):
    print(name, bool(Gst.ElementFactory.find(name)))
d = os.path.join(clips, 'hls-cmaf').replace(os.sep, '/')
os.makedirs(d, exist_ok=True)
src = os.path.join(clips, 'test_1080p30.mp4').replace(os.sep, '/')
desc = (f'filesrc location="{src}" ! qtdemux ! h264parse ! video/x-h264,stream-format=avc ! '
        f'hlscmafsink target-duration=2 playlist-type=vod playlist-location="{d}/index.m3u8" '
        f'location="{d}/seg%05d.m4s" init-location="{d}/init%05d.mp4"')
p = Gst.parse_launch(desc)
p.set_state(Gst.State.PLAYING)
msg = p.get_bus().timed_pop_filtered(60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
p.set_state(Gst.State.NULL)
print('packaged', msg.type if msg else None, sorted(os.listdir(d))[:4], len(os.listdir(d)))
