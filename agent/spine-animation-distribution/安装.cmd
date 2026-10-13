@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python spine-animation/scripts/install.py
) else (
  py -3 spine-animation/scripts/install.py
)
pause
