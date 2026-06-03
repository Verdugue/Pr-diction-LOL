@echo off
chcp 65001 >nul
title LoL Win Predictor - Launcher
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===============================================
echo   LoL Win Predictor - Launcher
echo ===============================================
echo.
echo Demarrage du dump Live API (20s) dans une fenetre separee...
start "LoL Live API Dump" cmd /c "%~dp0scripts\dump_live_loop.bat"
echo.
python launcher.py
echo.
echo Le launcher s'est arrete.
echo (Pense a fermer la fenetre "LoL Live API Dump" si elle est encore ouverte)
pause
