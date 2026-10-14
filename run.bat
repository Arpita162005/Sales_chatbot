@echo off
REM Start the chatbot (double-click or run from cmd). Run setup.ps1 once before.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No .venv found - run setup.ps1 first.
  pause
  exit /b 1
)
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" run.py
pause
