@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
where py >nul 2>&1
if !ERRORLEVEL!==0 (
  py -3 extension\skill\scripts\install-native-host.py
  set "status=!ERRORLEVEL!"
  goto done
)
where python >nul 2>&1
if !ERRORLEVEL!==0 (
  python extension\skill\scripts\install-native-host.py
  set "status=!ERRORLEVEL!"
  goto done
)
where python3 >nul 2>&1
if !ERRORLEVEL!==0 (
  python3 extension\skill\scripts\install-native-host.py
  set "status=!ERRORLEVEL!"
  goto done
)
echo Python 3 was not found. Install Python 3, then double-click this file again.
set "status=1"
:done
echo.
if "%status%"=="0" (
  echo Installed. Go back to Chrome and click the toolbar icon.
) else (
  echo Install did not finish. Read the message above.
)
pause
exit /b %status%
