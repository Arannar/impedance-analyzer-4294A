#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Serialize timer runs and manual invocations.
exec 9>/run/lock/impedance-analyzer-update.lock
flock -n 9 || exit 0

docker compose -f compose.deploy.yml pull
docker compose -f compose.deploy.yml up -d --no-build --wait --wait-timeout 120
