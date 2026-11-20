@echo off
echo Stopping pAIrtner Couples Bot...
set BOT_DIR=/mnt/c/Users/%USERNAME%/couples-bot
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose down"
echo Bot stopped.
pause
