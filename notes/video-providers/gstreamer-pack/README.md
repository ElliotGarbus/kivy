# GStreamer pack check (spike 8)

A Kivy app that plays a bundled clip, HLS fMP4 and DASH over HTTPS and an
HTTPS 404 through the `gstplayer` provider, then writes `packcheck.json`
(`PACKCHECK_OUT` overrides the path) and quits.

Built with kivyforge from a Kivy wheel of the `gstreamer-video-provider`
branch:

```powershell
# from the Kivy worktree, with Kivy's build dependencies installed
python -m pip wheel . --no-build-isolation --no-deps -w <this folder>\wheels
copy <spike clips>\big_buck_bunny_360_10s.mp4 src\clip.mp4
kivyforge lock -p windows
kivyforge package -p windows
```

Results in `results/`; write-up in `../GSTREAMER-CORE-SPIKES.md` (spike 8).
