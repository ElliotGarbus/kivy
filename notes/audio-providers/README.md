# Kivy 3.0 audio providers: notes

Branch `audio-provider-notes` on the fork. Nothing here goes into an
upstream PR.

| File | What |
|---|---|
| `AUDIO-PLAN.md` | The high-level plan: provider matrix, phases, decisions |
| `CONTRACTS.md` | The API and behavior every provider implements (C1-C8), and the open questions (O1-O3) |
| `MOBILE-AUDIO.md` | SDL3 versus native audio on Android and iOS, for the default order; spike SP8 |
| `PLAN-MAC.md` | Shared work (Python, `sdl3`, test suite, docs), macOS and iOS. Steps M0, M1, M2, M6, S1-S4 |
| `PLAN-WINDOWS.md` | Windows, Linux, Raspberry Pi and Android. Steps W1-W6 |
| `tools/` | `make_fixtures.py`, `format_probe.py` (M0), `bench_audio.py`, `summarize_audio.py` (from the rubicon spike) |
| `fixtures/` | Test audio for both machines (M0) |
| `results/` | One directory per platform and step, e.g. `results/pi5-w1/` |

## How the two machines work together

- The Mac owns the shared code and Apple; the Windows machine owns Windows,
  Linux, the Pi and Android. Each plan lists the shared files it may touch.
- Contracts first: both machines implement `CONTRACTS.md`. Provider branches
  start from `upstream/master` and test against the Mac's S1 branch by a
  local merge, then rebase when S1 merges.
- Hand-offs (one machine needs something from the other) go in the table
  below. The receiving machine marks them done.
- Before editing this directory: `git pull --rebase`. Edit only the status
  rows your machine owns. Commit and push right away, so the other machine
  sees it.

## Status

| Step | Owner | Branch / PR | Status |
|---|---|---|---|
| M0 fixtures and probe | Mac | `audio-provider-notes` | not started |
| SP1 spike: bad files in `sdl3`/`avplayer` | Mac | `audio-provider-notes` | waits for M0 |
| SP2 spike: raising from `on_source` | Mac | scratch | not started |
| SP3 spike: `AVAudioPlayer.play()` off the main thread | Mac | scratch | not started |
| SP4 spike: `AVPlayer` streams through rubicon-objc | Mac | scratch | not started |
| M1 `sdl3` first (phase 1) | Mac | `audio-sdl3-first` | worktree created |
| S1 contracts in `Sound`/`SoundLoader` | Mac | `audio-sound-contracts` | not started |
| S2 shared behavior suite | Mac | `audio-behavior-tests` | not started |
| S3 `avplayer` contract | Mac | `audio-avplayer-contract` | waits for #9530 |
| S4 `sdl3` contract | Mac | `audio-sdl3-contract` | waits for #9533 |
| M2 ffpyplayer removal (phase 2) | Mac | #9546 | open |
| M6 Apple streaming | Mac | `audio-avplayer-streaming` | not started |
| W1 SDL3 formats per platform, O2 measurement | Windows | `audio-provider-notes` | waits for M0 |
| W-SP1 spike: bad files on Windows, Linux, Pi, Android | Windows | `audio-provider-notes` | waits for M0 |
| SP5 spike: Media Foundation audio-only engine | Windows | scratch | not started |
| SP6 spike: GStreamer gapless loop | Windows | scratch | not started |
| SP7 spike: Media3 load wait across threads | Windows | scratch | not started |
| W2 phase 1 checks | Windows | none | waits for M1 |
| W3 `mediafoundation` (phase 3) | Windows | `audio-mediafoundation` | not started |
| W4a `gstplayer` contract | Windows | `audio-gstplayer-contract` | not started |
| W4b `android` (`MediaPlayer`) contract | Windows | `audio-android-contract` | not started |
| W4c `sdl3` contract checks | Windows | none | waits for S4 |
| W5 Media3 (phase 5) | Windows | `audio-android-media3` | not started |
| W6 streaming | Windows | per provider | not started |

PRs this depends on: [#9529](https://github.com/kivy/kivy/pull/9529),
[#9530](https://github.com/kivy/kivy/pull/9530),
[#9531](https://github.com/kivy/kivy/pull/9531) (rubicon-objc, avplayer),
[#9533](https://github.com/kivy/kivy/pull/9533) (SDL3 loops and events),
[#9546](https://github.com/kivy/kivy/pull/9546) (ffpyplayer removal).

## Hand-offs

| From | To | What | Done |
|---|---|---|---|
| Mac | Windows | M0: fixtures and `format_probe.py` pushed | |
| Mac | Windows | S1 branch pushed (API usable for local merges) | |
| Windows | Mac | W1: SDL3 decoder sets and AIFF `length` per platform (O2) | |
| Windows | Mac | W-SP1: bad-file behavior per provider (O4, with SP1) | |
| Windows | Mac | SP5, SP7: any change to C2's 2 s load timeout | |
| Windows | Mac | W2: phase 1 results on 4 platforms | |
| Windows | Mac | `_KNOWN_FAILURES` for `gstplayer`, `android`, `mediafoundation`, and `sdl3` on 4 platforms | |
| Windows | Mac | W4c: `sdl3` contract results on 4 platforms | |

## Contract changes

Record each change to `CONTRACTS.md` here with the date and the commit.

- 2026-11-24: O1 decided, `android` stays before `sdl3` on Android (C1).
  So `android` is no longer the last candidate there for local files: its
  Media3 backend must confirm a local file opens before `load()` returns
  (C2). O2 decided: map `AIFF` only if W1 finds its `length` correct.

- 2026-11-24: spikes added. C8's single `corrupt` fixture split into
  `notaudio` (tests 7 and 8) and `damaged` (test 10, defined after SP1).
  New open question O4.

## Open questions

In `CONTRACTS.md`: O4 (files with a valid header and damaged data; SP1 and
W-SP1 answer it before S1 starts) and O3 (`AVAudioPlayer.play()` off the
main thread; SP3). O1 and O2 are decided (above).

## Spikes

Each plan has a "Spikes" section: SP1-SP4 on the Mac, W-SP1 and SP5-SP7 on
the Windows machine. A spike answers one question inside a time box and
writes `results/spikes/<name>.md` before the step that depends on it
starts.
