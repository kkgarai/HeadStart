@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 extension\skill\scripts\uninstall-native-host.py
) else (
  python extension\skill\scripts\uninstall-native-host.py
)
echo.
echo Host removed. Remove the extension in chrome://extensions.
pause
