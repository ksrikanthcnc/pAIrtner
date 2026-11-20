@echo off
set BOT_DIR=/mnt/c/Users/%USERNAME%/couples-bot
wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose logs -f --tail=100"
pause
