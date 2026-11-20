@echo off
title pAIrtner Couples Bot
echo.
echo  ============================================
echo   pAIrtner Couples Bot
echo  ============================================
echo.
echo   Dashboard:  http://localhost:8765
echo   REST API:   http://localhost:8080
echo   Health:     http://localhost:8080/healthz
echo.
echo   Ctrl+C to stop containers
echo  ============================================
echo.

REM Edit BOT_DIR to the project path inside WSL
set BOT_DIR=/mnt/c/Users/%USERNAME%/couples-bot

echo Building and starting containers (foreground)...
echo.

wsl -d Ubuntu -- bash -c "cd %BOT_DIR% && docker compose up --build"

echo.
echo  Containers stopped.
pause
