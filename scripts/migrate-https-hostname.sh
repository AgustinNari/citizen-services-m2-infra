#!/usr/bin/env bash
# Operator-only hostname migration. Never called by application CI/CD.
set -Eeuo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -eq 0 || "$1" == --help || "$1" == -h ]]; then
  exec python3 "$script_dir/migrate_https_hostname.py" --help
fi
: "${HOME:?HOME is required}"
lock_dir="$HOME/DA2/.deploy"
mkdir -p -- "$lock_dir"
chmod 700 -- "$lock_dir"
# --close prevents descendants from retaining this lock after the operator exits.
exec flock --close -w 30 "$lock_dir/m2-deploy.lock" \
  env M2_HOSTNAME_LOCK_HELD=1 python3 "$script_dir/migrate_https_hostname.py" --state-dir "$lock_dir" "$@"
