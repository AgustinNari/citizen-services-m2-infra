#!/usr/bin/env bash
# Optional local preparation: private random values, written directly to .env.
set -euo pipefail
umask 077
[[ ! -e .env ]] || { echo '.env ya existe; no se sobrescribió.' >&2; exit 1; }
set -o noclobber
exec 3>.env
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  case "$line" in
    TRACKING_CODE_ENCRYPTION_KEY_BASE64=) line+="$(openssl rand -base64 32)" ;;
    POSTGRES_PASSWORD=|MOCK_CITIZEN_PASSWORD=|MOCK_AGENT_PASSWORD=|MOCK_AREA_RESPONSIBLE_PASSWORD=|MOCK_ADMIN_PASSWORD=|QA_RABBIT_PASSWORD=|QA_TRUSTSTORE_PASSWORD=)
      line+="$(openssl rand -hex 24)" ;;
  esac
  printf '%s\n' "$line" >&3
done < .env.example
exec 3>&-
chown --reference=.env.example .env
echo '.env local creado con valores aleatorios privados; no se imprimieron secretos.'
