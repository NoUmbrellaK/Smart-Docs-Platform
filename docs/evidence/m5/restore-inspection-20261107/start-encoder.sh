#!/usr/bin/env bash
# Existing local cache only; service startup validates model files.
set -euo pipefail
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
encoder_dir=$(mktemp -d /tmp/smartdocs-restore-inspection.XXXXXX)
chown nobody:nogroup "$encoder_dir"
printf 'encoder_dir=%s\n' "$encoder_dir"
systemd-run --no-block --unit=smartdocs-restore-inspection-encoder-20261107 --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Type=notify -p NotifyAccess=main -p TimeoutStartSec=190 -p RuntimeMaxSec=900 -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p TimeoutStopSec=5 -p KillMode=control-group -p PrivateNetwork=yes -p RestrictAddressFamilies=AF_UNIX --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m smartdocs.resident_service "$encoder_dir/encoder.sock" /tmp/smartdocs-safe.h4SzvX/vector-probe/model /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
