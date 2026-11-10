@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag your .pst file onto start.bat, or paste its full path below.
  set /p "PST_PATH=PST file path: "
) else (
  set "PST_PATH=%~1"
)
if not defined PST_PATH (
  echo No PST file specified.
  pause
  exit /b 1
)
py -3.12 app.py "%PST_PATH%"
echo.
echo Viewer stopped or an error occurred.
pause
