#!/usr/bin/env bash
# One SSH login: restart the Pi stream server detached and rerun stream-check.
cd "$(dirname "$0")"
/c/Windows/System32/OpenSSH/ssh.exe -o HostKeyAlias=raspberrypi.local \
  edgarbus@10.168.168.202 'bash -s' < pi_rerun.sh
