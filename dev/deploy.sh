#!/bin/bash
# Deploy pAIrtner from this checkout to the Win docker host.
#   bash dev/deploy.sh             sync files only
#   bash dev/deploy.sh --rebuild   sync, then rebuild + restart the bot, then print a health check
# Always syncs BEFORE building. Never touches .env or the live DB (see .rsyncignore).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)/"
# Host details live in dev/deploy.env (gitignored), e.g.:
#   DEPLOY_HOST=user@host   DEPLOY_DIR=/path/on/host   DEPLOY_WIN_DIR=E:/path/on/host (for scp, optional)
#   DEPLOY_WSL="wsl -d Ubuntu-26.04 -u root"   (only for a Windows host running Docker inside WSL)
[ -f "$(dirname "$0")/deploy.env" ] && . "$(dirname "$0")/deploy.env"
HOST="${DEPLOY_HOST:?set DEPLOY_HOST in dev/deploy.env}"
DIR="${DEPLOY_DIR:?set DEPLOY_DIR in dev/deploy.env}"
WSL="${DEPLOY_WSL:-}"
SCP_DIR="${DEPLOY_WIN_DIR:-$DIR}"

RSYNC_PATH="rsync"; [ -n "$WSL" ] && RSYNC_PATH="$WSL -e rsync"
rsync -avz --rsync-path="$RSYNC_PATH" --exclude-from="$SRC/.rsyncignore" "$SRC" "$HOST:$DIR/" | tail -3
# uv.lock is needed by the Docker build but is not in .rsyncignore on purpose; make sure it is current.
scp -q "$SRC/uv.lock" "$HOST:$SCP_DIR/uv.lock"

if [[ "${1:-}" == "--rebuild" ]]; then
  ssh "$HOST" "${WSL:+$WSL -e }docker compose -f $DIR/docker-compose.yml up -d --build pairtner" 2>&1 | tail -4
  ssh "$HOST" "${WSL:+$WSL -e }docker ps --filter name=pairtner --format {{.Names}}__{{.Status}}"
  echo "(logs: ssh $HOST '${WSL:+$WSL -e }docker logs pairtner-pairtner-1 --tail 20')"
else
  echo "Synced. Re-run with --rebuild to rebuild the container."
fi
