@echo off
echo Starting pAIrtner Couples Bot...
REM Edit BOT_DIR to the project path inside WSL
set BOT_DIR=/mnt/c/Users/%USERNAME%/couples-bot
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose up -d"
echo Bot started! Dashboard at http://localhost:8765
pause
