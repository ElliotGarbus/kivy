#!/usr/bin/env bash
# Runs as root (started by systemd-run, so it outlives the desktop session):
# stop the desktop, run the KMSDRM check as edgarbus, start the desktop again.
LOG=/home/edgarbus/gstreamer-spike/results/pi5-spike5/kms_all.log
mkdir -p "$(dirname "$LOG")"
{
  echo "start $(date)"
  systemctl stop lightdm
  sleep 3
  runuser -u edgarbus -- env HOME=/home/edgarbus bash /home/edgarbus/gstreamer-spike/kms_run.sh
  echo "check finished $(date)"
  systemctl start lightdm
  echo "desktop restarted $(date)"
} > "$LOG" 2>&1
chown -R edgarbus:edgarbus /home/edgarbus/gstreamer-spike/results/pi5-spike5
