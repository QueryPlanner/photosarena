#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s <host-port> <compose-project>\n' "$0" >&2
  exit 64
}

[[ $# -eq 2 ]] || usage
port=$1
project=$2

[[ $port =~ ^[0-9]{1,5}$ ]] || usage
(( port >= 1 && port <= 65535 )) || usage
[[ $project =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || usage

for command_name in docker ss; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Cannot check port %s: %s is unavailable.\n' "$port" "$command_name" >&2
    exit 69
  fi
done

published=$(docker ps -a --filter "publish=$port" \
  --format '{{.Names}}|{{.Label "com.docker.compose.project"}}')
other_projects=()
same_project=0

while IFS='|' read -r name owner; do
  [[ -n ${name:-} ]] || continue
  if [[ ${owner:-} == "$project" ]]; then
    same_project=1
  else
    other_projects+=("${name}:${owner:-unlabelled}")
  fi
done <<< "$published"

if ((${#other_projects[@]} > 0)); then
  printf 'Port %s conflicts with Docker container(s): %s\n' \
    "$port" "${other_projects[*]}" >&2
  exit 1
fi

tcp_listeners=$(ss -H -ltn "sport = :$port")
udp_listeners=$(ss -H -lun "sport = :$port")
if [[ -n $tcp_listeners || -n $udp_listeners ]]; then
  if (( same_project == 0 )); then
    printf 'Port %s already has a listening socket outside Compose project %s.\n' \
      "$port" "$project" >&2
    exit 1
  fi
fi

printf 'Port %s is available for Compose project %s.\n' "$port" "$project"
