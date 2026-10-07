@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 (
    python -m venv .venv
) else (
    py -m venv .venv
)
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Open http://127.0.0.1:5000/ in your browser.
echo Admin login: admin / admin. Local classroom demo only.
echo.
".venv\Scripts\python.exe" app.py
pause
exit /b
:failed
echo Setup failed. Check Python 3.10+ and your internet connection.
pause
exit /b 1
