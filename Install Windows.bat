@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 extension\skill\scripts\install-native-host.py
) else (
  python extension\skill\scripts\install-native-host.py
)
echo.
echo Go back to Chrome and click the toolbar icon.
pause
