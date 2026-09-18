@echo off
title Conectando Google Drive - Indomavel
cd /d "%~dp0"
echo ========================================================
echo Conectando sua conta ao Google Drive...
echo ========================================================
.venv\Scripts\python.exe ativar_drive.py
pause
