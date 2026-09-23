#!/usr/bin/env bash
# Restrict a repository deploy key to one digest-pinned PhotosArena deployment.
set -Eeuo pipefail

readonly EXPECTED_COMMAND_RE='^deploy (ghcr\.io/queryplanner/photosarena@sha256:[a-f0-9]{64})( ([A-Za-z0-9][A-Za-z0-9-]*))?$'
readonly SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly DEPLOY_SCRIPT="$SCRIPT_DIR/deploy.sh"

usage() {
  printf 'PhotosArena deploy key accepts only: deploy <image-digest> [ghcr-username]\n' >&2
  exit 64
}

original_command=${SSH_ORIGINAL_COMMAND:-}
[[ $original_command =~ $EXPECTED_COMMAND_RE ]] || usage
[[ -x $DEPLOY_SCRIPT ]] || { printf 'PhotosArena deploy helper is unavailable.\n' >&2; exit 69; }

image=${BASH_REMATCH[1]}
username=${BASH_REMATCH[3]:-queryplanner}
exec "$DEPLOY_SCRIPT" "$image" "$username"
