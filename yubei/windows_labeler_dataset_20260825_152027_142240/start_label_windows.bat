@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 yubei\label_server.py --session dataset --host 127.0.0.1 --port 8090 --open-browser
) else (
  python yubei\label_server.py --session dataset --host 127.0.0.1 --port 8090 --open-browser
)
pause
