@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo QueryWise's virtual environment was not found.
    echo Create it and install requirements.txt before using this launcher.
    pause
    exit /b 1
)

"%PYTHON%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501', timeout=2)" >nul 2>nul
if not errorlevel 1 (
    echo QueryWise is already running. Opening the existing app.
    start "" "http://localhost:8501"
    exit /b 0
)

echo Starting QueryWise from %~dp0
echo Keep this window open while you use the app. Press Ctrl+C here to stop it.
echo.

"%PYTHON%" -m streamlit run "%~dp0app.py"
if errorlevel 1 (
    echo.
    echo QueryWise stopped with an error. Read the message above for details.
    pause
)

endlocal
