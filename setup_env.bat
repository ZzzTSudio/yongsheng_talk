@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Pip uses Python's getproxies(): if no *_proxy env vars are set, Windows registry
REM (Internet Options proxy) is used. An HTTPS proxy URL like https://IP:port can trigger
REM "check_hostname requires server_hostname" with older pip/urllib3. Setting NO_PROXY=* makes
REM the env proxy map non-empty so registry proxies are skipped for this session.
set "NO_PROXY=*"

echo === Cyber Colleague - setup Python environment ===
echo.

if exist ".venv\Scripts\python.exe" (
  echo Virtual env already exists: .venv
  goto :install
)

echo Creating .venv ^(Python 3.10+^)...
where py >nul 2>&1
if errorlevel 1 goto :use_python
py -3 -m venv .venv
if errorlevel 1 goto :fail
goto :check_venv

:use_python
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Install Python 3.10+ from https://www.python.org/downloads/
  echo        and enable "Add python.exe to PATH", or install the "py" launcher.
  exit /b 1
)
python -m venv .venv
if errorlevel 1 goto :fail

:check_venv
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe missing.
  exit /b 1
)

:install
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

echo Upgrading pip...
python -m pip install --upgrade pip
echo Installing requirements...
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo OK. Environment ready. Next: run build_windows.bat
exit /b 0

:fail
echo.
echo setup_env.bat FAILED.
echo Press any key to close this window...
pause >nul
exit /b 1
