#!/usr/bin/env bash
# Install as the forced command of a dedicated SSH key, using the deploy user's HOME.
set -Eeuo pipefail
umask 077

if [[ $# -ne 0 || "${SSH_ORIGINAL_COMMAND-approve-core-outbox-v1}" != approve-core-outbox-v1 ]]; then
  echo 'Outbox command rejected.' >&2
  exit 64
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
lock_dir="${HOME:?}/DA2/.deploy"
mkdir -p -- "$lock_dir"
chmod 700 -- "$lock_dir"
exec 9>"$lock_dir/m2-deploy.lock"
if ! flock -w 600 9; then
  echo 'Outbox lock timeout; nothing changed.' >&2
  exit 75
fi
if [[ -e "$lock_dir/m2-maintenance" || -e "$lock_dir/m2-outbox-disabled" ]]; then
  echo 'Outbox approvals disabled by maintenance/operator.' >&2
  exit 75
fi
# The lock stays inherited by the process and its children, including on SSH disconnect.
exec timeout --signal=TERM --kill-after=10 900 python3 "$script_dir/core_outbox_approval.py"
