#!/bin/bash
# Deploy pAIrtner to WSL Docker on Windows.
# Run from Mac: ./dev/deploy-wsl.sh
#
# Prerequisites:
# - SSH access to Windows: ssh kalya@192.168.1.7
# - Docker running in WSL
# - Git repo at /home/kalya/pAIrtner (set up by sync-to-wsl.sh)

set -euo pipefail

WIN_HOST="${WIN_HOST:-kalya@192.168.1.7}"
WSL_DISTRO="Ubuntu-26.04"
WSL_REPO="/home/kalya/pAIrtner"

echo "==> Syncing code to WSL..."
cd "$(dirname "$0")/.."
git bundle create /tmp/pairtner.bundle master
scp /tmp/pairtner.bundle "${WIN_HOST}:pairtner.bundle"

# Write deploy script (avoids quoting issues)
cat > /tmp/wsl-deploy.sh << 'SCRIPT'
#!/bin/bash
set -e
cd /home/kalya/pAIrtner

# Sync from bundle
git fetch /mnt/c/Users/kalya/pairtner.bundle master:synced 2>/dev/null
git reset --hard synced
git branch -d synced 2>/dev/null

# Create .env if missing
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Created .env from example — edit it with your tokens!"
    echo "    nano /home/kalya/pAIrtner/.env"
fi

# Generate dashboard token if not set
if ! grep -q "DASHBOARD_TOKEN=" .env || [ "$(grep DASHBOARD_TOKEN .env | cut -d= -f2)" = "" ]; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    echo "DASHBOARD_TOKEN=${TOKEN}" >> .env
    echo "📋 Dashboard token: ${TOKEN}"
    echo "   Use this as Bearer token or in the login page"
fi

# Build and start
echo "==> Building Docker images..."
docker compose build --quiet 2>&1

echo "==> Starting bot..."
docker compose up -d pairtner 2>&1

echo "==> Starting dashboard..."
DASHBOARD_BIND=0.0.0.0 docker compose --profile dashboard up -d dashboard 2>&1

echo ""
echo "✅ Deployed!"
echo ""
echo "Services:"
echo "  Bot:       running (Telegram + REST)"
echo "  Dashboard: http://192.168.1.7:8765"
echo "  PWA:       http://192.168.1.7:8780 (if REST adapter enabled)"
echo ""
docker compose ps 2>&1
echo ""
echo "Logs: docker compose logs -f"
SCRIPT

scp /tmp/wsl-deploy.sh "${WIN_HOST}:wsl-deploy.sh"
ssh "${WIN_HOST}" "wsl -d ${WSL_DISTRO} -u root -e bash /mnt/c/Users/kalya/wsl-deploy.sh"
