@echo off
chcp 65001 >nul
REM Force Python itself to UTF-8; the codepage alone does not reach it.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Windows runs a .bat opened from inside a zip out of a temp copy, where
REM the rest of the kit does not exist. Catch that before it fails in a way
REM that looks like the kit is broken.
echo %~dp0 | find /I "\AppData\Local\Temp\" >nul
if not errorlevel 1 goto :not_extracted
if not exist "install.py" goto :not_extracted
goto :extracted

:not_extracted
echo.
echo   ============================================
echo     Stop - the folder is not extracted yet
echo   ============================================
echo.
echo   You are looking at the files inside the ZIP.
echo.
echo   Do this instead:
echo     1. Go back to the ZIP file
echo     2. Right-click it -^> Properties -^> tick "Unblock" -^> OK
echo     3. Right-click it -^> "Extract All..."
echo     4. Open the extracted folder and run 2-INSTALL again
echo.
pause
exit /b 1

:extracted
echo.
echo   ============================================
echo     Ari Coach - installation
echo   ============================================
echo.

REM Claude Desktop keeps a background process after its window closes, so
REM this warns rather than blocks -- a hard stop he cannot get past is worse
REM than a config write he may have to repeat.
tasklist 2>nul | find /I "claude.exe" >nul
if not errorlevel 1 (
  echo   [!] Claude Desktop still seems to be running in the background.
  echo.
  echo       That is normal - closing the window does not quit it.
  echo       If you can, quit it fully first ^(Task Manager -^> Claude -^> End task^).
  echo.
  echo       Or just press a key and carry on. If the connection does not
  echo       take, close Claude fully and run me again.
  echo.
  pause
)

REM uv manages Python itself, so this is the only prerequisite.
where uv >nul 2>&1
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  ) else (
    echo   Installing uv ^(one time, ~20 seconds^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  )
)

where uv >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [X] uv did not install. Close this window, reopen it, and try again.
  pause
  exit /b 1
)

uv run python install.py
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo   Done. Read what is printed above - two steps are left.
) else (
  echo   [X] Something failed. Send the text above to Alon.
)
echo.
pause
