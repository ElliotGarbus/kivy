# Audio plan: Mac (shared work, macOS, iOS)

Executed on the Mac by a Cursor agent, with the user steering. Covers the
work every platform shares (Python in `kivy/core/audio_output/`, the `sdl3`
provider, the shared test suite, docs) and the Apple provider (`avplayer`).
The Windows machine covers Windows, Linux, the Pi and Android
(`PLAN-WINDOWS.md`). Read `CONTRACTS.md` before any step; it fixes the API
both machines implement.

## Rules

- Every PR branch starts from `upstream/master` in its own worktree, is
  pushed to `origin` (the fork, `ElliotGarbus/kivy`) and opened against
  `kivy/kivy`. Notes never go into an upstream PR; they live on
  `audio-provider-notes`.
- No AI attribution in commits, PR titles, descriptions or comments.
- When a step finishes, update its row in `README.md` ("Status") on
  `audio-provider-notes`, then `git pull --rebase` and push. Edit only rows
  this machine owns.
- PR descriptions follow the existing ones (#9530, #9533): what changed,
  why, what was tested and on what hardware.

## Environment

- Checkouts live in `~/Developer/kivyPRs/`. `kivy/` is the user's main
  checkout; don't switch its branch. `kivy-ios/` holds the iOS dependency
  build; don't touch it. `kivy-audio-notes/` is this branch.
- New worktree for a PR:
  ```sh
  cd ~/Developer/kivyPRs/kivy && git fetch upstream
  git worktree add --no-track -b <branch> ../kivy-<short-name> upstream/master
  ```
- Build in place and test from the worktree root (don't use `env.sh`'s
  `kivy-rebuild` alias; it installs the main checkout):
  ```sh
  cd ~/Developer/kivyPRs/kivy-<short-name>
  source ../env.sh            # venv (Python 3.14) and KIVY_DEPS_ROOT
  python setup.py build_ext --inplace -j 10 > /tmp/<short-name>-build.log 2>&1
  python -c "import kivy; print(kivy.__file__)"   # must be this worktree
  python -m pytest -q -p no:cacheprovider kivy/tests/test_audio.py \
      kivy/tests/test_audio_provider.py kivy/tests/test_audio_android.py
  ruff check kivy/core/audio_output kivy/tests
  ```
- iOS wheels (device and Simulator; `kivyforge lock` needs both). From the
  worktree, with `ios-kivy-dependencies` symlinked to
  `../kivy-ios/ios-kivy-dependencies`:
  ```sh
  export KIVY_DEPS_ROOT=$PWD/ios-kivy-dependencies
  for arch in arm64_iphoneos arm64_iphonesimulator; do
    CIBW_PLATFORM=ios CIBW_ARCHS=$arch CIBW_BUILD='cp315-*' \
    CIBW_ENABLE=cpython-prerelease IPHONEOS_DEPLOYMENT_TARGET=16.0 \
    KIVY_SPLIT_EXAMPLES=1 ../ios-build/venv/bin/cibuildwheel \
      --output-dir ../ios-build/wheelhouse-<short-name>-$arch
    python3 tools/add-ios-frameworks.py ../ios-build/wheelhouse-<short-name>-$arch
  done
  git checkout kivy/_version.py   # the build rewrites it
  ```
- iOS device runs use the iPhone 18 Pro Max, UDID
  `00008160-0001192601A0000A`. kivyforge can't pass environment variables to
  the app, so a test app reads its scenario from a file in `src/`. The
  check app is `notes/audio-providers/ios-check/` (created in M1, copied
  from `scratch/ios-9533/`):
  ```sh
  cd ~/Developer/kivyPRs/kivy-audio-notes/notes/audio-providers/ios-check
  cp ../../../../ios-build/wheelhouse-<short-name>-*/kivy-*.whl wheels/
  # set the kivy== pin in pyproject.toml to the wheels' version
  kivyforge lock -p ios
  kivyforge run -p ios --device --destination 00008160-0001192601A0000A
  ```

## Order

| Step | What | Branch | Needs | Unblocks |
|---|---|---|---|---|
| M0 | Fixtures for both machines | `audio-provider-notes` | nothing | W1, S2 |
| M1 | Phase 1: `sdl3` first | `audio-sdl3-first` (worktree `kivy-audio-phase1` exists) | #9533, #9546 merged before it merges; W1 report before it merges | phase 1 checks on Windows |
| S1 | Contracts in `Sound`/`SoundLoader` | `audio-sound-contracts` | nothing | every provider step on both machines |
| S2 | Shared behavior test suite | `audio-behavior-tests` | S1 (stack locally), M0 | W4, W5 runs |
| S4 | `sdl3` meets the contracts | `audio-sdl3-contract` | S1, #9533 | W4 checks on 4 platforms |
| S3 | `avplayer` meets the contracts | `audio-avplayer-contract` | S1, #9530 | none |
| M2 | Keep #9546 current | `remove-ffpyplayer` | nothing | none |
| M6 | Phase 6: Apple streaming | `audio-avplayer-streaming` | S1, S3 | none |

Do M0 first (it's short and the Windows machine waits on it), then S1, since
both machines' provider work needs it. M1 can be prepared alongside S1 but
merges after #9533 and #9546.

---

## M0. Fixtures

Goal: one set of audio files both machines test with, including the AAC
formats only the Mac can encode (`afconvert`).

1. In `notes/audio-providers/tools/make_fixtures.py` (copied from the
   rubicon spike), change the output to `notes/audio-providers/fixtures/`
   and the signals to:
   - `short1`: 1.0 s, 440 Hz sine, 48 kHz mono, in
     `wav flac mp3 ogg opus aiff m4a aac caf`.
   - `tone10`: 10.0 s, 440 Hz with a click every second, same formats
     (for loop, seek and drift checks by ear).
   - `corrupt.<ext>` for the same extensions: the first 64 bytes of the
     matching `short1` file followed by 4 KiB of random bytes (seeded, so
     reruns produce identical files).
   - `manifest.json`: true durations (from `afinfo`, or `soundfile` where
     CoreAudio can't read the format), sizes and SHA-256.
2. `pip install numpy soundfile` in the venv if missing; run it.
3. Write `notes/audio-providers/tools/format_probe.py`, the one script both
   machines use to see what each provider does with each file (W1, M1,
   phase checks):
   - A minimal Kivy `App` (so run loops that deliver framework callbacks
     run, as in `bench_audio.py`) that exits when done. Also importable as
     `run_probe(fixtures_dir, out_path, providers=None)` for the mobile
     check apps.
   - Records the platform, Kivy version, `SoundLoader.available_providers()`,
     `SoundSDL3.extensions()`, and every "Unrecognized audio decoder"
     warning (capture `Logger` output), which together give the SDL3
     decoder set.
   - For each `short1.*`, `corrupt.*` and `tone10.*`: which provider the
     default `SoundLoader.load()` picks; then for each provider that claims
     the extension, with `audio_output_provider=name`: load result or
     exception text, `length`, and for `short1` the time from `play()` to
     `on_stop` (and `on_eos` once S1 exists), with a 5 s timeout.
   - Prints one `APROBE {json}` line per result and writes all of them to
     `out_path` (default `results/<platform>-<label>/probe.json`).
   - Test it on macOS against the fixtures.
4. Commit the fixtures (they are small: check `du -sh`; keep under 5 MB) and
   the probe, and push. Mark M0 done in `README.md`.

Done when: `fixtures/`, `manifest.json` and `tools/format_probe.py` are on
`origin/audio-provider-notes`.

## M1. Phase 1: `sdl3` first

Branch `audio-sdl3-first`, worktree `~/Developer/kivyPRs/kivy-audio-phase1`
(already created from `upstream/master`).

Code:

1. **Order (C1).** In `kivy/core/audio_output/__init__.py`: on Android,
   append `android` then `sdl3` (unchanged from today apart from
   `ffpyplayer`, which #9546 removes); everywhere else, `sdl3` first (when
   `USE_SDL3`), then `avplayer` on macOS and iOS, then `gstplayer` in its
   existing `try`. Reorder
   `PROVIDER_CONFIGS['audio_output']` in `kivy/core/__init__.py` to
   `sdl3, avplayer, mediafoundation, android, gstplayer`, but leave
   `mediafoundation` out until phase 3 adds the module (the Windows
   machine adds it then). Delete the orphaned "Little trick here, don't
   activate gstreamer on window" comment and the comment above `gstplayer`
   that explains it was put after `sdl3`.
2. **Decoder names.** In `SoundSDL3.extensions()`
   (`kivy/core/audio_output/audio_sdl3.pyx`), replace the `if/elif` chain
   with a module-level dict from SDL3_mixer 3.2.0's decoder names
   (`src/decoder_*.c`) to extensions:

   | Decoder | Extensions |
   |---|---|
   | `WAV` | wav |
   | `VORBIS`, `STBVORBIS` | ogg, oga |
   | `OPUS` | opus |
   | `FLAC`, `DRFLAC` | flac |
   | `MPG123`, `DRMP3` | mp3 |
   | `AIFF` | aiff, aif (only if W1 finds `length` correct; O2) |
   | `AU` | au |
   | `VOC` | voc |
   | `WAVPACK` | wv |
   | `XMP` | mod, xm, it, s3m, mtm, stm, 669, far, med, okt, ult, umx |
   | `GME` | spc, nsf, nsfe, gbs, gym, hes, kss, sap, vgm, vgz, ay |

   Skip `SINEWAVE`, `RAW`, `TIMIDITY` and `FLUIDSYNTH` silently (the last
   two are MIDI; see AUDIO-PLAN "Decisions"). Keep the warning for names
   not in the dict. Check the `XMP` and `GME` lists against the libxmp and
   game-music-emu versions SDL3_mixer 3.2.0 vendors (the Linux build runs
   `external/download.sh`; the Mac's copy in
   `~/Developer/kivyPRs/kivy-deps-build/kivy-dependencies/build/SDL3_mixer-3.2.0/`
   has the script but not the sources) and drop any extension they don't
   read.
3. **Docs.**
   - `audio_output/__init__.py` module docstring: a short "Provider order"
     paragraph (SDL3 first; the platform's provider plays what SDL3
     can't; on Android the platform player stays first so audio keeps
     playing in the background, and `audio_output_provider='sdl3'` gives
     lower latency for sound effects).
   - `doc/sources/guide/environment.rst`, `KIVY_AUDIO_OUTPUT`: the
     default order per platform.
   - `doc/sources/migration.rst`, Audio section: "`sdl3` is tried first on
     every platform except Android" with the consequence (macOS/iOS:
     mp3/wav now play through SDL3, not AVFoundation; Android: unchanged),
     and correct #9533's sentence that tracker formats aren't supported
     (they play on builds whose SDL3_mixer has `XMP`, per the W1 report).
4. **Tests** (`kivy/tests/test_audio_provider.py`): the registration order
   per platform (patch `platform` and reload, or assert on
   `PROVIDER_CONFIGS` plus the append order); the decoder dict maps every
   name above and none of the MIDI ones.

Verify:

- macOS: build, run the tests, and `tools/format_probe.py` against the
  branch; save to `results/macos-m1/probe.json`. Expected default picks:
  wav/flac/mp3/ogg on `SoundSDL3`, m4a/aac/caf/aiff on `SoundAvplayer`.
- iOS: create `notes/audio-providers/ios-check/` from `scratch/ios-9533/`
  (rename the project, bundle id `org.kivy.audio-check`). Its `main.py`
  copies `format_probe.py` and the fixtures into `src/` at prepare time
  and calls `run_probe()`. Run it on the device and save the log and JSON
  to `results/ios-m1/`.
- Before merging: the W1 report (Windows, Linux, Pi, Android decoder sets
  and the O2 AIFF measurement) is in `README.md`; apply O2's rule and
  update the migration note with the results.

PR title: "Audio: try the sdl3 provider first on desktop and iOS".

## S1. Contracts in `Sound` and `SoundLoader`

Branch `audio-sound-contracts`. Implements C2-C7's base-class parts. Python
only, so it runs on every platform's CI and on Android by overlay.

1. `kivy/core/audio_output/__init__.py`:
   - `SoundLoadError` (C2), exported in `__all__`.
   - `Sound`: `__events__` gains `on_eos` and `on_error`; `state` gains
     `'pause'`; `pause()`, `resume()`, `on_eos()`, `on_error()`,
     `_do_eos()`, `_do_error()`, `schemes()` with the docstrings in
     `CONTRACTS.md`; `_constructed` set at the end of `__init__`;
     `on_source` re-raises during construction and calls `_do_error`
     after.
   - `SoundLoader.load()`: collect failures, one aggregated warning, strict
     mode raises `SoundLoadError` (C2); URL routing by `schemes()` (C6),
     replacing the `content://` special case; `resource_find` skipped for
     URLs.
   - `SoundAndroidPlayer.schemes()` returns `('content',)`
     (`audio_android.py`); it's the only provider change in this PR.
   - `pitch` docstring: drop "only implemented for SDL3"; describe C7's
     per-provider behavior.
2. `kivy/core/__init__.py`: check `load_with_provider_selection()` can
   carry the failure list (it's shared with image and text; keep their
   behavior unchanged).
3. Tests in a new `kivy/tests/test_audio_contracts.py`, with fake providers
   registered on a fresh `SoundLoader` state (no audio device): fallback
   order; aggregated warning text; `None` vs strict-mode raise; `on_source`
   after construction dispatches `on_error`; `_do_eos` and `_do_error`
   event order and state; pause/resume state rules; scheme routing,
   `file://` as a local path, unknown scheme warning; `content://` still
   goes to `android` (extend the existing `TestContentUriRouting`).
4. Docs: the module docstring gets "Errors", "End of stream" and "Pausing"
   sections; `migration.rst` Audio section: new events and states, and
   that `SoundLoader.load()` now tries the next provider when one fails.
5. Push the branch as soon as the API compiles and the unit tests pass,
   before polishing docs: the Windows machine merges it locally for W3-W5.
   Note the push in `README.md`.

PR title: "Audio: pause, on_eos, on_error and provider fallback on load
failure". Mention that it supersedes #8609 and answers #2156.

## S2. Shared behavior test suite

Branch `audio-behavior-tests`, stacked locally on S1 until S1 merges.

1. Copy `fixtures/short1.*` and `fixtures/corrupt.*` into
   `kivy/tests/audio_fixtures/` (check `MANIFEST.in`/`pyproject.toml` so
   they ship with the tests like other test data).
2. Write `kivy/tests/test_audio_behavior.py` as specified in C8, with
   `_KNOWN_FAILURES` filled in for every provider by running it on macOS
   (sdl3, avplayer) and from the W-machine's first run (the Windows
   machine posts its list in `README.md`, "Hand-offs").
3. CI: the suite skips without an audio device; confirm on the GitHub
   macOS, Windows and Ubuntu jobs that it skips cleanly or passes.

PR title: "Audio: one behavior test suite for every audio provider".

## S4. `sdl3` meets the contracts

Branch `audio-sdl3-contract`, after #9533 merges (it reworks the same
file). Shared code: the Windows machine verifies it on Windows, Linux, the
Pi and Android (W4).

1. `load()`: raise `SoundLoadError` with `SDL_GetError()` when
   `MIX_LoadAudio` returns NULL; log at debug, not warning.
2. `pause()`/`resume()`: `MIX_PauseTrack`/`MIX_ResumeTrack`; `get_pos()`
   while paused; `_check_play` must not treat a paused track as finished
   (`MIX_TrackPaused`).
3. End of sound: where `_check_play` currently calls `stop()` for a
   finished, non-looping sound, call `_do_eos()`.
4. `play()` failure (`MIX_PlayTrack` false): `_do_error`.
5. Remove `sdl3` from `_KNOWN_FAILURES`; run S2's suite on macOS and on the
   iPhone (extend `ios-check` with a "behavior" scenario that runs the same
   checks in-app).

PR title: "SDL3 audio: pause, on_eos and errors through on_error".

## S3. `avplayer` meets the contracts

Branch `audio-avplayer-contract`, from `upstream/master` after #9530 merges
(until then, from `origin/avplayer-rubicon-objc`, rebased later).

1. `load()`: `AVAudioPlayer.alloc().initWithContentsOfURL_error_()`
   returning nil raises `SoundLoadError` with the `NSError`'s
   `localizedDescription` (pass an `ObjCInstance` error pointer).
2. `pause()`/`resume()`: `pause` and `play` on the player.
3. End of sound: the delegate's `audioPlayerDidFinishPlaying:successfully:`
   calls `_do_eos()` when not looping; `successfully == NO` and
   `audioPlayerDecodeErrorDidOccur:error:` call `_do_error`.
4. iOS session category: when `avplayer` plays before SDL3 has opened its
   device, set the same category SDL would (Playback with mixing and
   ducking, or `KIVY_AUDIO_CATEGORY`), through `AVAudioSession`, so an app
   that only plays m4a/aac isn't left in SoloAmbient. Reuse #9533's
   category table rather than a second copy.
5. O3: measure calling `play()` on a serial dispatch queue (latency, and
   1,000 rapid play/stop/pause cycles without a crash or a stuck state) on
   the Mac and the iPhone. Ship it only if it's reliable; otherwise keep
   `play()` on the main thread and document "use wav/ogg/mp3 for sound
   effects". Record the numbers in `results/s3-play-thread.md`.
6. Remove `avplayer` from `_KNOWN_FAILURES`; run the suite on macOS and in
   `ios-check`.

PR title: "avplayer audio: pause, on_eos, errors, and the iOS session
category".

## M2. Keep #9546 current

#9546 (removes ffpyplayer and the `ffmpeg` video provider) is open. Rebase
it when master moves. After M1 merges, update its "What replaces it" table:
with `sdl3` first, Opus plays through SDL3 on builds that have the `OPUS`
decoder (W1 says which), not `gstplayer`.

## M6. Phase 6: Apple streaming

Branch `audio-avplayer-streaming`, after S1 and S3.

`AVAudioPlayer` can't open URLs. Add an `AVPlayer` path to the `avplayer`
provider for URL sources:

1. `schemes()` returns `('http', 'https')`. `load()` picks the
   `AVAudioPlayer` path for local files and an `AVPlayer` path for URLs.
2. The `AVPlayer` path polls from the Clock (no KVO): `currentItem.status`
   for ready or failed (C2: return immediately, it's the only provider for
   the scheme on Apple, and report failures through `on_error`),
   `currentTime`, `duration` (`length` 0 for live streams), rate for
   pause/resume. End of item through `AVPlayerItemDidPlayToEndTimeNotification`
   or by polling `currentTime >= duration`; loop by seeking to zero.
3. Errors: `currentItem.error` and `AVPlayerItemFailedToPlayToEndTime` call
   `_do_error` with the error's description (follow the wording of #9555,
   which names the cause for video).
4. Test with the stream list from `notes/video-providers/stream-check/` on
   the `video-provider-notes` branch (HTTP MP3/AAC, HLS audio-only), on
   macOS and the iPhone.
5. Docs: which schemes each platform's provider opens (a table in the
   module docstring, filled in by both machines' phase 6 PRs).

PR title: "avplayer audio: play http and https streams".
