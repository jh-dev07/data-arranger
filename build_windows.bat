@echo off
rem Build the stand-alone Windows app: dist\DataArranger\DataArranger.exe
rem Needs: Python 3.8 (py launcher), Visual Studio Build Tools (C++) + CMake for llama.cpp,
rem        7-Zip installed (for CHM support). The CI workflow does the same steps.
setlocal
cd /d "%~dp0"
set CMAKE_ARGS=-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DLLAMA_CURL=OFF
set FORCE_CMAKE=1
if not exist .venv\Scripts\python.exe (
    py -3.8 -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --upgrade pip wheel setuptools
.venv\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=5.13,<6.10" || goto :error
.venv\Scripts\python.exe download_models.py || goto :error
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name DataArranger ^
    --collect-all rapidocr_onnxruntime --collect-all llama_cpp ^
    --collect-binaries onnxruntime --collect-data langdetect ^
    --collect-submodules hachoir run.py || goto :error
xcopy /e /i /y models dist\DataArranger\models >nul
if exist "%ProgramFiles%\7-Zip\7z.exe" (
    mkdir dist\DataArranger\tools\7z 2>nul
    copy /y "%ProgramFiles%\7-Zip\7z.exe" dist\DataArranger\tools\7z\ >nul
    copy /y "%ProgramFiles%\7-Zip\7z.dll" dist\DataArranger\tools\7z\ >nul
)
if exist tools xcopy /e /i /y tools dist\DataArranger\tools >nul
echo.
echo Built: %~dp0dist\DataArranger\DataArranger.exe
echo Copy the whole dist\DataArranger folder to install it on another PC.
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
