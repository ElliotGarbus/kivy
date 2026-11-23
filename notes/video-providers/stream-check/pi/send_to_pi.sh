#!/usr/bin/env bash
# Send this worktree's Kivy and the stream-check app to the Pi in one SSH
# session (one password), then build and run there (pi_run.sh).
set -eu
cd "$(dirname "$0")"
P=payload
rm -rf "$P"; mkdir -p "$P"
git -C ../.. archive --format=tar -o "$PWD/$P/kivy.tar" HEAD
cp -r /c/Users/ellio/PycharmProjects/KivyPRs-video-notes/notes/video-providers/stream-check/src "$P/src"
rm -f "$P"/src/results-*.jsonl
cp pi_run.sh "$P/"
echo "payload: $(du -sh "$P" | cut -f1)"
tar -cf - -C "$P" . | /c/Windows/System32/OpenSSH/ssh.exe -o HostKeyAlias=raspberrypi.local \
  edgarbus@10.168.168.202 \
  'rm -rf ~/stream-check-run && mkdir -p ~/stream-check-run && tar -xf - -C ~/stream-check-run && bash ~/stream-check-run/pi_run.sh'
