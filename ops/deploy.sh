#!/usr/bin/env bash
# Host contract: Compose service "app", image from PHOTOSARENA_IMAGE,
# persistent writable /data mount, and app.backup/app.migrate module commands.
set +x
set -Eeuo pipefail

readonly EXPECTED_IMAGE_RE='^ghcr\.io/queryplanner/photosarena@sha256:[a-f0-9]{64}$'
readonly DEPLOY_DIR="${PHOTOSARENA_DEPLOY_DIR:-/srv/photosarena}"
readonly COMPOSE_PROJECT="${PHOTOSARENA_COMPOSE_PROJECT:-photosarena}"
readonly HOST_PORT="${PHOTOSARENA_HOST_PORT:-8182}"
readonly COMPOSE_SERVICE="${PHOTOSARENA_COMPOSE_SERVICE:-app}"
readonly COMPOSE_FILE="${PHOTOSARENA_COMPOSE_FILE:-$DEPLOY_DIR/compose.yaml}"
readonly IMAGE_STATE_FILE="${PHOTOSARENA_IMAGE_STATE_FILE:-$DEPLOY_DIR/.deployed-image}"
readonly PORT_CHECK="${PHOTOSARENA_PORT_CHECK:-$(cd "$(dirname "$0")" && pwd)/check-host-port.sh}"
readonly HEALTH_URL="${PHOTOSARENA_HEALTH_URL:-http://127.0.0.1:${HOST_PORT}/healthz}"
readonly HEALTH_ATTEMPTS="${PHOTOSARENA_HEALTH_ATTEMPTS:-20}"
readonly HEALTH_INTERVAL="${PHOTOSARENA_HEALTH_INTERVAL:-2}"
ghcr_username="${PHOTOSARENA_GHCR_USERNAME:-queryplanner}"

usage() {
  printf 'Usage: %s ghcr.io/queryplanner/photosarena@sha256:<64 lowercase hex> [ghcr-username]\n' "$0" >&2
  printf 'Read a short-lived GHCR token from stdin.\n' >&2
  exit 64
}

fail() {
  printf 'PhotosArena deploy: %s\n' "$*" >&2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
new_image=$1
if [[ $# -eq 2 ]]; then
  ghcr_username=$2
fi
[[ $new_image =~ $EXPECTED_IMAGE_RE ]] || usage
[[ $ghcr_username =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || usage
[[ $HOST_PORT =~ ^[0-9]{1,5}$ ]] && (( HOST_PORT >= 1 && HOST_PORT <= 65535 )) || usage
[[ $HEALTH_ATTEMPTS =~ ^[1-9][0-9]*$ ]] || usage
[[ $HEALTH_INTERVAL =~ ^[0-9]+([.][0-9]+)?$ ]] || usage
[[ $COMPOSE_PROJECT =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || usage

for command_name in docker curl flock mktemp date sleep; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "required command '$command_name' is unavailable"
    exit 69
  fi
done
[[ -f $COMPOSE_FILE ]] || { fail "Compose file not found: $COMPOSE_FILE"; exit 66; }
[[ -x $PORT_CHECK ]] || { fail "port check is missing or not executable: $PORT_CHECK"; exit 66; }

umask 077
mkdir -p "$DEPLOY_DIR"
exec 9>"$DEPLOY_DIR/.deploy.lock"
if ! flock -n 9; then
  fail 'another deployment holds the lock'
  exit 75
fi

cd "$DEPLOY_DIR"
docker_config=$(mktemp -d "${TMPDIR:-/tmp}/photosarena-docker.XXXXXX")
chmod 700 "$docker_config"
cleanup() {
  if [[ -n ${docker_config:-} && -d $docker_config ]]; then
    rm -rf -- "$docker_config"
  fi
}
trap cleanup EXIT
export DOCKER_CONFIG="$docker_config"

previous_image=''
if [[ -f $IMAGE_STATE_FILE ]]; then
  IFS= read -r previous_image < "$IMAGE_STATE_FILE" || true
  if [[ ! $previous_image =~ $EXPECTED_IMAGE_RE ]]; then
    fail 'stored image digest is invalid; refusing to deploy without a rollback target'
    exit 65
  fi
else
  running_container=$(docker compose --project-name "$COMPOSE_PROJECT" \
    --file "$COMPOSE_FILE" ps -q "$COMPOSE_SERVICE")
  if [[ -n $running_container ]]; then
    fail 'an app container is running but the saved image digest is missing; refusing an unsafe update'
    exit 65
  fi
fi

if ! "$PORT_CHECK" "$HOST_PORT" "$COMPOSE_PROJECT"; then
  fail 'host port preflight failed'
  exit 1
fi

ghcr_token=''
IFS= read -r ghcr_token || [[ -n $ghcr_token ]] || true
if [[ -z $ghcr_token ]]; then
  fail 'GHCR token was empty on stdin'
  exit 65
fi
if ! printf '%s' "$ghcr_token" | docker login ghcr.io \
  --username "$ghcr_username" --password-stdin >/dev/null; then
  unset ghcr_token
  fail 'GHCR login failed'
  exit 1
fi
unset ghcr_token

compose() {
  local image=$1
  shift
  PHOTOSARENA_IMAGE="$image" docker compose --project-name "$COMPOSE_PROJECT" \
    --file "$COMPOSE_FILE" "$@"
}

if ! compose "$new_image" pull "$COMPOSE_SERVICE"; then
  fail 'could not pull the requested image digest'
  exit 1
fi
docker logout ghcr.io >/dev/null 2>&1 || true

backup_name="photosarena-$(date -u +%Y%m%dT%H%M%SZ)-$$.sqlite3"
backup_destination="${PHOTOSARENA_BACKUP_DEST:-/data/backups/$backup_name}"
if [[ $backup_destination != /data/* ]]; then
  fail 'PHOTOSARENA_BACKUP_DEST must be under the persistent /data mount'
  exit 64
fi
if ! compose "$new_image" run --rm --no-deps \
  -e "PHOTOSARENA_BACKUP_DEST=$backup_destination" \
  "$COMPOSE_SERVICE" python -m app.backup; then
  fail 'SQLite online backup failed; the current app was left untouched'
  exit 1
fi

rollback() {
  local reason=$1
  fail "$reason; attempting rollback"
  if [[ -n $previous_image ]]; then
    if ! compose "$previous_image" pull "$COMPOSE_SERVICE"; then
      fail 'could not refresh the previous image; trying its local copy'
    fi
    if compose "$previous_image" up -d "$COMPOSE_SERVICE" && wait_for_health; then
      fail "restored previous image $previous_image"
      return 0
    fi
    fail 'previous image did not become healthy; manual recovery is required'
    return 1
  fi
  if compose "$new_image" stop "$COMPOSE_SERVICE"; then
    fail 'no prior image existed; stopped the unhealthy first deployment'
  else
    fail 'no prior image existed and the unhealthy service could not be stopped'
  fi
  return 1
}

deployment_started=0
handle_unexpected_error() {
  local status=$1
  local line=$2
  if (( deployment_started )); then
    rollback "unexpected shell failure at line $line (status $status)" || true
  fi
  exit "$status"
}
trap 'handle_unexpected_error "$?" "$LINENO"' ERR

wait_for_health() {
  local attempt
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if curl --fail --silent --show-error --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    if (( attempt < HEALTH_ATTEMPTS )); then
      sleep "$HEALTH_INTERVAL"
    fi
  done
  return 1
}

if ! compose "${previous_image:-$new_image}" stop "$COMPOSE_SERVICE"; then
  rollback 'could not stop the current app before migration' || true
  exit 1
fi
deployment_started=1
if ! compose "$new_image" run --rm --no-deps "$COMPOSE_SERVICE" python -m app.migrate; then
  rollback 'database migration failed' || true
  exit 1
fi
if ! compose "$new_image" up -d "$COMPOSE_SERVICE"; then
  rollback 'new app failed to start' || true
  exit 1
fi
if ! wait_for_health; then
  rollback 'new app failed the health check' || true
  exit 1
fi

if ! state_tmp=$(mktemp "$DEPLOY_DIR/.deployed-image.XXXXXX"); then
  rollback 'could not create the image state file' || true
  exit 1
fi
if ! printf '%s\n' "$new_image" > "$state_tmp" || ! chmod 644 "$state_tmp"; then
  rm -f -- "$state_tmp"
  rollback 'could not prepare the image state file' || true
  exit 1
fi
if ! mv -f -- "$state_tmp" "$IMAGE_STATE_FILE"; then
  rm -f -- "$state_tmp"
  rollback 'could not persist the new image digest' || true
  exit 1
fi

printf 'PhotosArena deploy healthy: %s\n' "$new_image"
