@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Tip: keep the repo on a local SSD; network drives slow PyInstaller a lot.
REM Re-running the same spec reuses some analysis; usually faster than the first build.

echo === Cyber Colleague - build CyberColleague.exe ===
echo.

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: No .venv found. Run setup_env.bat first.
  exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

where pyinstaller >nul 2>&1
if errorlevel 1 (
  echo ERROR: pyinstaller not found. Run setup_env.bat first.
  exit /b 1
)

echo Running PyInstaller...
pyinstaller --noconfirm cyber_colleague.spec
if errorlevel 1 goto :fail

if not exist "dist\CyberColleague.exe" (
  echo ERROR: dist\CyberColleague.exe not found after build.
  exit /b 1
)

copy /Y "dist\CyberColleague.exe" "%~dp0CyberColleague.exe" >nul
echo.
echo OK: %~dp0CyberColleague.exe
echo     ^(copy of dist\CyberColleague.exe^)

echo Verifying exe starts ^(3s, then close if still running^)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\verify_exe.ps1" "%~dp0dist\CyberColleague.exe"
if errorlevel 1 (
  echo ERROR: exe quit with error during verification ^(often missing DLL / import^).
  exit /b 1
)

echo Verification OK.
exit /b 0

:fail
echo.
echo build_windows.bat FAILED.
echo Press any key to close this window...
pause >nul
exit /b 1
