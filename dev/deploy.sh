#!/bin/bash
# Deploy pAIrtner from this checkout to the Win docker host.
#   bash dev/deploy.sh             sync files only
#   bash dev/deploy.sh --rebuild   sync, then rebuild + restart the bot, then print a health check
# Always syncs BEFORE building. Never touches .env or the live DB (see .rsyncignore).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)/"
HOST="${DEPLOY_HOST:-kalya@192.168.1.7}"
WSL="wsl -d Ubuntu-26.04 -u root"
DIR="/mnt/e/media/docker/pairtner"

rsync -avz --rsync-path="$WSL -e rsync" --exclude-from="$SRC/.rsyncignore" "$SRC" "$HOST:$DIR/" | tail -3
# uv.lock is needed by the Docker build but is not in .rsyncignore on purpose; make sure it is current.
scp -q "$SRC/uv.lock" "$HOST:E:/media/docker/pairtner/uv.lock"

if [[ "${1:-}" == "--rebuild" ]]; then
  ssh "$HOST" "$WSL -e docker compose -f $DIR/docker-compose.yml up -d --build pairtner" 2>&1 | tail -4
  ssh "$HOST" "$WSL -e docker ps --filter name=pairtner --format {{.Names}}__{{.Status}}"
  echo "(logs: ssh $HOST '$WSL -e docker logs pairtner-pairtner-1 --tail 20')"
else
  echo "Synced. Re-run with --rebuild to rebuild the container."
fi
