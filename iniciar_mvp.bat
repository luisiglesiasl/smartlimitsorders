@echo off
title ShieldOrder B2B - Smart Limit Orders MVP
cd /d "%~dp0backend"
echo Iniciando motor B2B y simulador web en http://127.0.0.1:8000 ...
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
