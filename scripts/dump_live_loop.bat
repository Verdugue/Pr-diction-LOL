@echo off
chcp 65001 >nul
title LoL Live API - Dump 20s (curl)
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set API_URL=https://127.0.0.1:2999/liveclientdata/allgamedata
set OUT_DIR=data\live_games
set INTERVAL=20
set TMP_JSON=%TEMP%\lol_live_dump.json

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

set GAME_FILE=

echo ===============================================
echo   LoL Live API - Dump 20s (curl)
echo ===============================================
echo Endpoint   : %API_URL%
echo Sortie     : %OUT_DIR%\raw_^<timestamp^>.jsonl
echo Intervalle : %INTERVAL%s
echo (Ctrl+C ou fermer la fenetre pour quitter)
echo.

:loop
set STATUS=000
for /f "delims=" %%S in ('curl -k -s -o "%TMP_JSON%" -w "%%{http_code}" "%API_URL%" 2^>nul') do set STATUS=%%S

if "!STATUS!"=="200" (
    if not defined GAME_FILE (
        for /f "delims=" %%T in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-ddTHH-mm-ss')"') do set TS=%%T
        set GAME_FILE=%OUT_DIR%\raw_!TS!.jsonl
        echo [!TIME:~0,8!] Partie detectee -^> !GAME_FILE!
    )
    powershell -NoProfile -Command "Get-Content -Raw -Encoding UTF8 '%TMP_JSON%' | ConvertFrom-Json | ConvertTo-Json -Compress -Depth 100" >> "!GAME_FILE!"
    echo [!TIME:~0,8!] Snapshot append
) else (
    if defined GAME_FILE (
        echo [!TIME:~0,8!] Partie terminee -^> !GAME_FILE! ferme
        set GAME_FILE=
    ) else (
        echo [!TIME:~0,8!] En attente d'une partie ^(HTTP !STATUS!^)
    )
)

timeout /t %INTERVAL% /nobreak >nul
goto loop
