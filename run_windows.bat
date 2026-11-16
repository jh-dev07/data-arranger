@echo off
rem Run Data Arranger from source (first run creates a virtual environment).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.8 -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
)
start "" .venv\Scripts\pythonw.exe run.py %*
exit /b 0
:error
echo Setup failed. Make sure Python 3.8 is installed (the "py" launcher must find it).
pause
exit /b 1
