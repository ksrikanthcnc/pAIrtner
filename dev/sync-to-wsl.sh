#!/bin/bash
# Sync local repo to WSL on Windows, then optionally push to GitHub.
# Usage: ./dev/sync-to-wsl.sh [--push]
#
# Flow: bundle local repo → SCP to Windows → pull in WSL → (optional) push to GitHub

set -euo pipefail

WIN_HOST="${WIN_HOST:-kalya@192.168.1.7}"
BUNDLE_PATH="/tmp/pairtner.bundle"
WIN_BUNDLE="pairtner.bundle"
WSL_REPO="/home/kalya/pAIrtner"
WSL_DISTRO="Ubuntu-26.04"

echo "==> Bundling local repo..."
git bundle create "$BUNDLE_PATH" --all

echo "==> SCP to Windows..."
scp "$BUNDLE_PATH" "${WIN_HOST}:${WIN_BUNDLE}"

echo "==> Pulling into WSL repo..."
# Write a temp script to avoid quoting issues
SCRIPT=$(mktemp /tmp/wsl-pull-XXXX.sh)
cat > "$SCRIPT" << 'EOF'
#!/bin/bash
cd /home/kalya/pAIrtner
git fetch /mnt/c/Users/kalya/pairtner.bundle master:synced
git merge synced --ff-only
git branch -d synced
echo "Synced. $(git log --oneline -1)"
EOF

scp "$SCRIPT" "${WIN_HOST}:wsl-pull.sh"
ssh "$WIN_HOST" "wsl -d ${WSL_DISTRO} -u kalya -e bash /mnt/c/Users/kalya/wsl-pull.sh"
rm "$SCRIPT"

if [[ "${1:-}" == "--push" ]]; then
    echo "==> Pushing to GitHub from WSL..."
    PUSH_SCRIPT=$(mktemp /tmp/wsl-push-XXXX.sh)
    cat > "$PUSH_SCRIPT" << 'EOF'
#!/bin/bash
cd /home/kalya/pAIrtner
git push origin master
echo "Pushed. $(git log --oneline -1)"
EOF
    scp "$PUSH_SCRIPT" "${WIN_HOST}:wsl-push.sh"
    ssh "$WIN_HOST" "wsl -d ${WSL_DISTRO} -u kalya -e bash /mnt/c/Users/kalya/wsl-push.sh"
    rm "$PUSH_SCRIPT"
fi

echo "==> Done."
