@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Installing PST Mail Viewer dependencies for Python 3.12...
py -3.12 -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Check Python 3.12 and your internet connection.
) else (
  echo.
  echo Installation completed.
)
pause
