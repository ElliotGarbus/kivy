"""Compare the avplayer (Rubicon) and sdl3 audio providers on macOS.

Runs inside a real Kivy App so the SDL event pump drives the main run loop,
exactly as in an application (AVAudioPlayer delegate callbacks are delivered
on that run loop).

All timings are API-level: they measure what ``get_pos()``, ``length`` and
``on_stop`` report to Kivy, not acoustic output.

Usage:
    python make_fixtures.py               # once
    python bench_audio.py --all --runs 3  # spawns one process per run
    python bench_audio.py --provider avplayer --run 0   # single run

Writes ``results/audio_<provider>_<run>.json``; ``summarize_audio.py`` turns
those into the tables in results.md.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
OUT = HERE / "results"
FORMATS = ["wav", "aiff", "flac", "mp3", "ogg", "opus", "m4a", "aac", "caf"]
VOLUME = float(os.environ.get("BENCH_VOLUME", "0.1"))

now = time.monotonic


def provider_class(name):
    if name == "avplayer":
        from kivy.core.audio_output.audio_avplayer import SoundAvplayer
        return SoundAvplayer
    from kivy.core.audio_output.audio_sdl3 import SoundSDL3
    return SoundSDL3


def is_loaded(sound):
    if hasattr(sound, "_avplayer"):
        return sound._avplayer is not None
    return bool(sound._audiotrack.is_audio_set())


async def sleep(sec):
    await asyncio.sleep(sec)


async def wait_for(pred, timeout, step=0.001):
    end = now() + timeout
    while now() < end:
        if pred():
            return True
        await sleep(step)
    return pred()


def linear_residual_ms(samples):
    """Std-dev of get_pos around a straight line fit, in ms."""
    if len(samples) < 3:
        return None
    ts = [t for t, _ in samples]
    ps = [p for _, p in samples]
    mt, mp = statistics.fmean(ts), statistics.fmean(ps)
    var = sum((t - mt) ** 2 for t in ts)
    slope = sum((t - mt) * (p - mp) for t, p in samples) / var
    res = [p - (mp + slope * (t - mt)) for t, p in samples]
    return statistics.pstdev(res) * 1000


async def bench_tone(cls, ext, manifest):
    """Load, length, start latency, drift, jitter and seek on tone10."""
    row = {"format": ext}
    path = str(FIX / f"tone10.{ext}")
    row["declared"] = ext in cls.extensions()
    t0 = now()
    try:
        s = cls(source=path)
    except Exception as e:
        row.update(loaded=False, error=repr(e))
        return row
    row["load_ms"] = (now() - t0) * 1000
    row["loaded"] = is_loaded(s)
    if not row["loaded"]:
        return row
    s.volume = VOLUME
    nominal = manifest[f"tone10.{ext}"]["nominal"]
    row["length"] = s.length
    row["length_err_ms"] = (s.length - nominal) * 1000

    stops_during_play_call = []
    s.bind(on_stop=lambda *a: stops_during_play_call.append(1))
    t_play = now()
    s.play()
    row["play_call_ms"] = (now() - t_play) * 1000
    row["play_dispatches_on_stop"] = bool(stops_during_play_call)
    started = await wait_for(lambda: s.get_pos() > 0, 1.0)
    row["plays"] = bool(started)
    row["start_latency_ms"] = (now() - t_play) * 1000 if started else None
    if not started:
        s.stop()
        s.unload()
        return row

    samples = []
    end = t_play + 6.5
    while now() < end:
        samples.append((now(), s.get_pos()))
        await sleep(0.05)
    window = [(t, p) for t, p in samples if t - t_play >= 0.5]
    (t1, p1), (t2, p2) = window[0], window[-1]
    row["drift_ms"] = ((p2 - p1) - (t2 - t1)) * 1000
    row["drift_window_s"] = t2 - t1
    row["pos_jitter_ms"] = linear_residual_ms(window)

    for target in (7.0, 2.5):
        s.seek(target)
        immediate = s.get_pos()
        await sleep(0.2)
        later = s.get_pos()
        key = str(target).replace(".", "_")
        row[f"seek{key}_immediate_err_ms"] = (immediate - target) * 1000
        row[f"seek{key}_after200ms_err_ms"] = (later - 0.2 - target) * 1000
    s.stop()
    s.unload()
    return row


async def bench_short(cls, ext, manifest):
    """on_stop latency, loop behaviour on short1; pitch rate on tone10."""
    row = {}
    path = str(FIX / f"short1.{ext}")
    s = cls(source=path)
    if not is_loaded(s):
        return row
    s.volume = VOLUME
    length = manifest[f"short1.{ext}"]["nominal"]

    events = {"stop": [], "play": []}
    s.bind(on_stop=lambda *a: events["stop"].append(now()),
           on_play=lambda *a: events["play"].append(now()))
    events["play"].clear()
    t_play = now()
    s.play()
    # SoundSDL3.play() calls stop() first, which dispatches on_stop
    events["stop"].clear()
    started = await wait_for(lambda: s.get_pos() > 0, 1.0)
    row["short_plays"] = bool(started)
    if not started:
        s.unload()
        return row
    t_started = now()
    fired = await wait_for(lambda: events["stop"], 3.0)
    row["natural_on_stop_fired"] = bool(fired)
    if fired:
        row["on_stop_late_ms"] = (events["stop"][0] - t_play - length) * 1000
        row["on_stop_late_from_start_ms"] = (
            events["stop"][0] - t_started - length) * 1000
    row["state_after_end"] = s.state

    # loop
    events["stop"].clear()
    events["play"].clear()
    s.loop = True
    samples = []
    t_play = now()
    s.play()
    events["stop"].clear()
    events["play"].clear()
    end = t_play + 3.6
    while now() < end:
        samples.append((now(), s.get_pos()))
        await sleep(0.005)
    # events dispatched while looping, before our own stop()
    row["loop_on_stop_events"] = len(events["stop"])
    row["loop_on_play_events"] = len(events["play"])
    s.loop = False
    s.stop()
    wraps, glitches = [], 0
    for (ta, pa), (tb, pb) in zip(samples, samples[1:]):
        if pa > 0.8 * length and pb < 0.2 * length:
            wraps.append(tb)
        elif pb < pa - 0.01:
            glitches += 1
    row["loop_wraps_in_3_6s"] = len(wraps)
    row["loop_pos_glitches"] = glitches
    if len(wraps) >= 2:
        periods = [b - a for a, b in zip(wraps, wraps[1:])]
        row["loop_overhead_ms"] = (statistics.fmean(periods) - length) * 1000
    # longest stretch where get_pos does not advance after the first wrap
    if wraps:
        stall, longest = None, 0.0
        for (ta, pa), (tb, pb) in zip(samples, samples[1:]):
            if ta < wraps[0] - 0.05:
                continue
            if pb <= pa + 1e-6:
                stall = stall or ta
                longest = max(longest, tb - stall)
            else:
                stall = None
        row["loop_max_stall_ms"] = longest * 1000
    s.unload()

    # pitch / rate: how fast does get_pos advance at pitch=1.5?
    s = cls(source=str(FIX / f"tone10.{ext}"))
    s.volume = VOLUME
    s.pitch = 1.5
    s.play()
    await sleep(0.5)
    t1, p1 = now(), s.get_pos()
    await sleep(2.0)
    t2, p2 = now(), s.get_pos()
    row["pitch1_5_pos_rate"] = (p2 - p1) / (t2 - t1)
    s.stop()
    s.unload()
    return row


async def bench_stress(cls):
    import psutil
    proc = psutil.Process()
    row = {}
    for ext in ("wav", "mp3"):
        rss0 = proc.memory_info().rss
        t0 = now()
        sounds = [cls(source=str(FIX / f"tone10.{ext}")) for _ in range(20)]
        row[f"load20_{ext}_ms"] = (now() - t0) * 1000
        row[f"load20_{ext}_rss_mb"] = (proc.memory_info().rss - rss0) / 2**20
        playing = sounds[:8]
        for s in playing:
            s.volume = VOLUME / 8
        proc.cpu_percent(None)
        t0 = now()
        for s in playing:
            s.play()
        row[f"play8_{ext}_start_all_ms"] = (now() - t0) * 1000
        await sleep(0.5)
        p1 = [s.get_pos() for s in playing]
        await sleep(2.5)
        p2 = [s.get_pos() for s in playing]
        row[f"play8_{ext}_cpu_pct"] = proc.cpu_percent(None)
        row[f"play8_{ext}_all_advancing"] = all(
            b - a > 2.0 for a, b in zip(p1, p2))
        row[f"play8_{ext}_spread_ms"] = (max(p2) - min(p2)) * 1000
        for s in sounds:
            s.stop()
            s.unload()
        del sounds, playing
        await sleep(0.3)
    return row


async def run_bench(provider, run):
    manifest = json.loads((FIX / "manifest.json").read_text())
    cls = provider_class(provider)
    result = {"provider": provider, "run": run, "formats": [], "stress": {}}
    for ext in FORMATS:
        row = await bench_tone(cls, ext, manifest)
        if row.get("loaded"):
            row.update(await bench_short(cls, ext, manifest))
        result["formats"].append(row)
        print(provider, run, ext, "loaded" if row.get("loaded") else "-",
              flush=True)
    result["stress"] = await bench_stress(cls)
    OUT.mkdir(exist_ok=True)
    (OUT / f"audio_{provider}_{run}.json").write_text(
        json.dumps(result, indent=2))


def run_app(provider, run):
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    from kivy.app import App
    from kivy.uix.label import Label

    class BenchApp(App):
        def build(self):
            return Label(text=f"bench_audio {provider} run {run}")

        def on_start(self):
            async def go():
                try:
                    await run_bench(provider, run)
                finally:
                    self.stop()
            asyncio.get_event_loop().create_task(go())

    asyncio.run(BenchApp().async_run(async_lib="asyncio"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["avplayer", "sdl3"])
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    if args.all:
        for run in range(args.runs):
            for provider in ("avplayer", "sdl3"):
                subprocess.run([sys.executable, __file__, "--provider",
                                provider, "--run", str(run)], check=True)
        return
    run_app(args.provider, args.run)


if __name__ == "__main__":
    main()
