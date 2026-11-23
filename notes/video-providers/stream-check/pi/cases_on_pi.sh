#!/usr/bin/env bash
# One SSH login: run the given stream-check cases on the Pi, one process each.
#   ./cases_on_pi.sh CASE...
cd "$(dirname "$0")"
/c/Windows/System32/OpenSSH/ssh.exe -o HostKeyAlias=raspberrypi.local \
  edgarbus@10.168.168.202 "bash -s -- $*" < pi_cases.sh
