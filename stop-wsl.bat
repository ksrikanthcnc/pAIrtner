@echo off
echo Stopping pAIrtner Couples Bot...
set BOT_DIR=/mnt/e/media/docker/pairtner
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose down"
echo Bot stopped.
pause
