@echo off
rem Double-click to start Ops Hub on this PC, then open http://localhost:8000
rem First run installs everything (needs Python 3.11+ from python.org, with "Add to PATH" ticked).
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo First run: setting up Ops Hub...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -e . || (echo Setup failed. Is Python 3.11 or newer installed? & pause & exit /b 1)
)
if not exist .env copy .env.example .env >nul
start "" "http://localhost:8000/#setup"
echo Ops Hub is running at http://localhost:8000  (close this window to stop)
.venv\Scripts\python -m opshub serve --host 127.0.0.1 --port 8000
