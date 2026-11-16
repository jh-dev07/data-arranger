@echo off
rem Build a stand-alone Windows app: dist\DataArranger\DataArranger.exe
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.8 -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=5.13,<6.10" || goto :error
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed ^
    --name DataArranger ^
    --collect-data langdetect ^
    --collect-submodules hachoir ^
    run.py || goto :error
echo.
echo Built: %~dp0dist\DataArranger\DataArranger.exe
echo Copy the whole dist\DataArranger folder to install it on another PC.
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
