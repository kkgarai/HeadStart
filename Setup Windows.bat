@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "%~dp0manifest.json" if exist "%~dp0setup-headstart.py" (
  call :runpy "%~dp0setup-headstart.py"
  goto finish
)
where git >nul 2>&1
if errorlevel 1 (
  echo Git is required.
  set "status=1"
  goto finish
)
set "dest=%USERPROFILE%\HeadStart"
if not exist "%dest%\.git" (
  git clone --branch dev --single-branch https://github.com/kkgarai/HeadStart.git "%dest%"
  if errorlevel 1 (
    set "status=1"
    goto finish
  )
)
call :runpy "%dest%\setup-headstart.py"
goto finish

:runpy
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~1"
  set "status=%ERRORLEVEL%"
  goto :eof
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%~1"
  set "status=%ERRORLEVEL%"
  goto :eof
)
where python3 >nul 2>&1
if not errorlevel 1 (
  python3 "%~1"
  set "status=%ERRORLEVEL%"
  goto :eof
)
echo Python 3 is required.
set "status=1"
goto :eof

:finish
echo.
if "%status%"=="0" (
  echo Setup finished. Load unpacked the folder printed above, then click the toolbar icon.
) else (
  echo Setup did not finish. Read the message above.
)
pause
exit /b %status%
