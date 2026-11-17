#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  echo "Usage: $0 <backend|frontend> <full-git-sha>" >&2
  echo "       $0 frontend ghcr.io/santimussi/front-desarrollo-apps-2@sha256:<64-lowercase-hex>" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 64
fi

service="$1"
selection="$2"

case "$service" in
  backend)
    image_key="BACKEND_IMAGE"
    image_repository="ghcr.io/juanmaguida/backend-damii"
    ;;
  frontend)
    image_key="FRONTEND_IMAGE"
    image_repository="ghcr.io/santimussi/front-desarrollo-apps-2"
    ;;
  *)
    echo "Unsupported service: $service" >&2
    usage
    exit 64
    ;;
esac

is_frontend_digest() {
  local digest="${1#"$image_repository@sha256:"}"
  [[ "$service" == frontend && "$1" == "$image_repository@sha256:$digest" && "$digest" =~ ^[0-9a-f]{64}$ ]]
}

valid_image_reference() {
  local revision="${1#"$image_repository:sha-"}"
  [[ "$1" == "$image_repository:sha-$revision" && "$revision" =~ ^[0-9a-f]{40}$ ]] || is_frontend_digest "$1"
}

if [[ "$selection" =~ ^[0-9a-fA-F]{40}$ ]]; then
  new_image="$image_repository:sha-${selection,,}"
elif is_frontend_digest "$selection"; then
  new_image="$selection"
else
  echo "Use a full Git SHA, or an exact allowed Frontend repository reference pinned by sha256 digest." >&2
  exit 64
fi

for command_name in docker flock curl awk mktemp python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 69
  fi
done

: "${HOME:?HOME is required}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "$script_dir/.." && pwd)"
compose_dir="$repository_root/compose"
compose_file="$compose_dir/compose.deploy.yml"
runtime_env="$compose_dir/.env"
images_env="$compose_dir/images.env"
smoke_script="$script_dir/smoke-https.sh"
integration_validator="$script_dir/validate-integration-config.py"
lock_dir="$HOME/DA2/.deploy"
lock_file="$lock_dir/m2-deploy.lock"
candidate_file=""

cleanup() {
  if [[ -n "$candidate_file" && -f "$candidate_file" ]]; then
    rm -f -- "$candidate_file"
  fi
}
trap cleanup EXIT

for required_file in "$compose_file" "$runtime_env" "$images_env" "$smoke_script" "$integration_validator"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required file not found: $required_file" >&2
    exit 66
  fi
done

mkdir -p -- "$lock_dir"
chmod 700 -- "$lock_dir"
exec 9>"$lock_file"

echo "Waiting for the shared deployment lock..."
if ! flock -w 600 9; then
  echo "Could not acquire $lock_file within 600 seconds." >&2
  exit 75
fi

if [[ -e "$lock_dir/m2-maintenance" ]]; then
  echo "Deployment blocked by $lock_dir/m2-maintenance; HTTPS migration/maintenance in progress." >&2
  exit 75
fi

cd -- "$compose_dir"

compose_with_images() {
  local selected_images_env="$1"
  shift
  docker compose \
    --project-directory "$compose_dir" \
    --env-file "$runtime_env" \
    --env-file "$selected_images_env" \
    -f "$compose_file" \
    "$@"
}

read_image() {
  local selected_key="$1"
  awk -v target="$selected_key" '
    index($0, target "=") == 1 {
      value = substr($0, length(target) + 2)
      sub(/\r$/, "", value)
      print value
      found = 1
    }
    END { if (!found) exit 1 }
  ' "$images_env"
}

make_candidate_file() {
  local selected_key="$1"
  local selected_image="$2"
  local temporary_file

  temporary_file="$(mktemp "$compose_dir/.images.env.candidate.XXXXXX")"
  awk -v target="$selected_key" -v replacement="$selected_image" '
    index($0, target "=") == 1 {
      print target "=" replacement
      found = 1
      next
    }
    { print }
    END { if (!found) print target "=" replacement }
  ' "$images_env" > "$temporary_file"
  chmod --reference="$images_env" "$temporary_file"
  candidate_file="$temporary_file"
}

smoke_once() {
  bash "$smoke_script" "$https_hostname" "$legacy_http_ip"
}

wait_for_smoke() {
  local phase="$1"
  local attempt

  for attempt in $(seq 1 20); do
    if smoke_once; then
      echo "$phase smoke tests passed on attempt $attempt."
      return 0
    fi

    if [[ "$attempt" -lt 20 ]]; then
      sleep 5
    fi
  done

  echo "$phase smoke tests failed after 20 attempts." >&2
  return 1
}

show_failure_diagnostics() {
  compose_with_images "$1" ps || true
  compose_with_images "$1" logs --tail=100 "$service" || true
  compose_with_images "$1" logs --tail=100 caddy frontend || true
}

ensure_previous_image_available() {
  # The last known image was already running here. Do not make recovery depend
  # on GHCR being reachable when its immutable SHA image is still cached.
  if docker image inspect "$previous_image" >/dev/null 2>&1; then
    echo "Using the previous immutable image from local cache for rollback."
    return 0
  fi
  compose_with_images "$images_env" pull "$service"
}

check_running_backend_provider() {
  local backend_id inspected
  local -a values
  backend_id="$(compose_with_images "$images_env" ps -q backend)" || return 1
  if [[ ! "$backend_id" =~ ^[0-9a-f]{12,64}$ ]]; then
    echo "Cannot verify the running Backend; no Frontend deployment is allowed." >&2
    return 1
  fi
  # Select only running state and provider. Never emit other container environment values.
  inspected="$(docker inspect --format '{{println .State.Running}}{{range .Config.Env}}{{if eq (index (split . "=") 0) "IDENTITY_PROVIDER"}}{{println .}}{{end}}{{end}}' "$backend_id")" || return 1
  mapfile -t values <<< "$inspected"
  [[ "${values[0]:-}" == true ]] || { echo "Backend is not running." >&2; return 1; }
  # Images before explicit provider configuration used the documented MOCK default.
  if [[ "${#values[@]}" -eq 1 ]]; then
    [[ "$backend_provider" == MOCK ]] && return 0
  elif [[ "${#values[@]}" -eq 2 && "${values[1]}" == "IDENTITY_PROVIDER=$backend_provider" ]]; then
    return 0
  fi
  echo "Running Backend provider differs from the validated configuration; recreate Backend separately first." >&2
  return 1
}

check_frontend_image_provider() {
  local reference="$1" provider
  provider="$(docker image inspect --format '{{if .Config.Labels}}{{index .Config.Labels "ar.edu.uade.m2.identity-provider"}}{{end}}' "$reference")" || return 1
  if [[ "$provider" == MOCK || "$provider" == M1 ]]; then
    [[ "$provider" == "$backend_provider" ]] && return 0
  elif [[ -z "$provider" || "$provider" == '<no value>' ]]; then
    if [[ "$backend_provider" == MOCK && "$reference" == "$image_repository:sha-"* ]]; then
      echo "Legacy Frontend without provider metadata: permitted only with MOCK Backend." >&2
      return 0
    fi
  fi
  echo "Frontend provider metadata is missing, invalid or incompatible with Backend." >&2
  return 1
}

# Let Compose perform dotenv interpolation; never source private runtime files.
# Only extract nonsecret proxy settings, never print the rendered configuration.
proxy_settings="$(compose_with_images "$images_env" config --format json | python3 "$integration_validator" --deployment-settings)"
read -r https_hostname legacy_http_ip backend_provider <<< "$proxy_settings"

# Fail before touching an image if migration is incomplete or TLS is already down.
if ! smoke_once; then
  echo "HTTPS preflight failed; no image or images.env was changed. See docs/https.md." >&2
  exit 1
fi

previous_image="$(read_image "$image_key")" || {
  echo "$image_key is missing from $images_env." >&2
  exit 65
}

if ! valid_image_reference "$previous_image"; then
  echo "$image_key must reference an allowed immutable SHA tag or Frontend digest in $image_repository." >&2
  exit 65
fi

make_candidate_file "$image_key" "$new_image"

if [[ "$service" == frontend ]]; then
  check_running_backend_provider || exit 65
  if ! compose_with_images "$candidate_file" pull frontend; then
    echo "Frontend pull failed; no service was recreated." >&2
    exit 1
  fi
  check_frontend_image_provider "$new_image" || exit 65
  check_running_backend_provider || exit 65
fi

echo "Deploying $service with the selected immutable reference..."
deployment_healthy=false
if { [[ "$service" == frontend ]] || compose_with_images "$candidate_file" pull "$service"; } && \
   compose_with_images "$candidate_file" up -d --no-deps "$service" && \
   wait_for_smoke "Deployment"; then
  deployment_healthy=true
fi

if [[ "$deployment_healthy" == "true" ]]; then
  mv -f -- "$candidate_file" "$images_env"
  candidate_file=""
  compose_with_images "$images_env" ps
  echo "Deployment completed; $image_key now points to $new_image."
  exit 0
fi

echo "Deployment failed. Showing limited diagnostics before rollback." >&2
show_failure_diagnostics "$candidate_file"
rm -f -- "$candidate_file"
candidate_file=""

echo "Rolling $service back to its previous immutable image..." >&2
if ensure_previous_image_available && \
   compose_with_images "$images_env" up -d --no-deps --pull never "$service" && \
   wait_for_smoke "Rollback"; then
  compose_with_images "$images_env" ps
  if [[ "$service" == frontend ]] && { ! check_frontend_image_provider "$previous_image" || ! check_running_backend_provider; }; then
    echo "Previous Frontend image restored, but identity providers require coordinated operator recovery. Backend/configuration were not changed." >&2
    exit 2
  fi
  echo "Rollback succeeded; the requested deployment was not applied." >&2
  exit 1
fi

echo "Rollback failed. The service requires manual intervention." >&2
show_failure_diagnostics "$images_env"
exit 2
