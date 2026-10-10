@echo off
rem Double-click to power off all connected Tundra trackers.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tundra-off.ps1" %*
timeout /t 3 >nul
