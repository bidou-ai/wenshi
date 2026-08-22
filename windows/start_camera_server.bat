@echo off
setlocal
cd /d "%~dp0"

py -c "import flask, cv2, numpy, pyrealsense2" >nul 2>&1
if errorlevel 1 (
  echo Missing camera dependencies.
  echo Run once: py -m pip install -r requirements-windows.txt
  pause
  exit /b 1
)

echo Starting D435 service on port 18080...
py windows_realsense_server.py --host 0.0.0.0 --port 18080 %*
set "WENSHI_CAMERA_EXIT=%errorlevel%"
echo Camera service stopped with exit code %WENSHI_CAMERA_EXIT%.
pause
exit /b %WENSHI_CAMERA_EXIT%
