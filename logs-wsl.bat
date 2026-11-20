@echo off
set BOT_DIR=/mnt/e/media/docker/pairtner
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose logs -f --tail=100"
pause
