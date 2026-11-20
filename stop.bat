@echo off
echo Stopping pAIrtner containers...
set BOT_DIR=/mnt/c/Users/%USERNAME%/couples-bot
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose down"
echo Containers stopped.
pause
