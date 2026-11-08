@echo off
setlocal
cd /d "%~dp0"
if "%PULSE_SERVER_URL%"=="" set PULSE_SERVER_URL=https://pulse.bubuki.dev
start "Pulse Desktop Agent" pythonw.exe "%~dp0desktop_agent.py" --server "%PULSE_SERVER_URL%"
echo Pulse Desktop Agent started in background (0%% idle CPU, zero-polling WebSocket).
