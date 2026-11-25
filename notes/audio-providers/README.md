# Kivy 3.0 audio providers: notes

Branch `audio-provider-notes` on the fork. Nothing here goes into an
upstream PR.

| File | What |
|---|---|
| `AUDIO-PLAN.md` | The high-level plan: provider matrix, phases, decisions |
| `CONTRACTS.md` | The API and behavior every provider implements (C1-C8), and the open questions (O1-O3) |
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
| M1 `sdl3` first (phase 1) | Mac | `audio-sdl3-first` | worktree created |
| S1 contracts in `Sound`/`SoundLoader` | Mac | `audio-sound-contracts` | not started |
| S2 shared behavior suite | Mac | `audio-behavior-tests` | not started |
| S3 `avplayer` contract | Mac | `audio-avplayer-contract` | waits for #9530 |
| S4 `sdl3` contract | Mac | `audio-sdl3-contract` | waits for #9533 |
| M2 ffpyplayer removal (phase 2) | Mac | #9546 | open |
| M6 Apple streaming | Mac | `audio-avplayer-streaming` | not started |
| W1 SDL3 formats per platform, O1, O2 | Windows | `audio-provider-notes` | waits for M0 |
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
| Windows | Mac | W1: SDL3 decoder sets, O1 and O2 results | |
| Windows | Mac | W2: phase 1 results on 4 platforms | |
| Windows | Mac | `_KNOWN_FAILURES` for `gstplayer`, `android`, `mediafoundation`, and `sdl3` on 4 platforms | |
| Windows | Mac | W4c: `sdl3` contract results on 4 platforms | |

## Contract changes

None yet. Record each change to `CONTRACTS.md` here with the date and the
commit.

## Open questions

O1 (Android background audio with `sdl3` first), O2 (AIFF through SDL3)
and O3 (`AVAudioPlayer.play()` off the main thread) are in `CONTRACTS.md`.
O1 and O2 need W1's results; the user decides them before M1 merges.
