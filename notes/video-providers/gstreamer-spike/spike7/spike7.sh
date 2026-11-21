#!/usr/bin/env bash
# Run in WSL: spike 7 in podman containers (Debian 12 = GStreamer 1.22, Ubuntu 24.04 = 1.24).
set -u
SP=/mnt/c/Users/ellio/AppData/Local/Temp/claude/9b3f1a9fc8e797f5/d2cbc443-e105-4ff2-a0e8-49f3627a4d0a/scratchpad
NOTES=/mnt/c/Users/ellio/PycharmProjects/KivyPRs-video-notes/notes/video-providers/gstreamer-spike
rm -rf /tmp/s7 && mkdir -p /tmp/s7/src /tmp/s7/spike /tmp/s7/out
tar -xzf "$SP/kivy-gst-src.tar.gz" -C /tmp/s7/src
cp "$NOTES"/core_headless.py "$NOTES"/slow_http.py /tmp/s7/spike/
cp -r "$NOTES/clips" /tmp/s7/spike/clips
find /tmp/s7 -name "*.py" -o -name "*.sh" | xargs sed -i 's/\r$//'
sed 's/\r$//' "$SP/spike7_in_container.sh" > /tmp/s7/run.sh
podman --version
for spec in "debian12 docker.io/library/debian:12" "ubuntu2404 docker.io/library/ubuntu:24.04"; do
  set -- $spec
  echo; echo "############ $1 ($2)"
  podman run --rm -v /tmp/s7/src:/src:ro -v /tmp/s7/spike:/spike -v /tmp/s7/out:/out \
    -v /tmp/s7/run.sh:/run.sh:ro "$2" bash /run.sh "$1" 2>&1 | grep -v "^Trying to pull\|^Getting image\|^Copying \|^Writing manifest\|^Storing signatures"
done
mkdir -p "$NOTES/results/spike7"
cp /tmp/s7/out/*.json "$NOTES/results/spike7/" 2>/dev/null
ls "$NOTES/results/spike7/"
