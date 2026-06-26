@echo off
setlocal EnableExtensions
title MO UX Preview

chcp 65001 >nul 2>nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "EXIT_CODE=0"
if not defined UX_WIDTH set "UX_WIDTH=120"

mode con: cols=%UX_WIDTH% lines=40 >nul 2>nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

cd /d "%REPO_ROOT%" || (
  echo Failed to enter repo root: %REPO_ROOT%
  set "EXIT_CODE=1"
  goto :end
)

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo Python was not found on PATH.
  echo Install Python 3.10+ or add it to PATH, then run this file again.
  set "EXIT_CODE=1"
  goto :end
)

set "HAS_WIDTH=0"
for %%A in (%*) do (
  if /i "%%~A"=="--width" set "HAS_WIDTH=1"
)

if "%~1"=="" (
  set "UX_ARGS=--width %UX_WIDTH%"
) else if "%HAS_WIDTH%"=="0" (
  set "UX_ARGS=--width %UX_WIDTH% %*"
) else (
  set "UX_ARGS=%*"
)

echo Starting MO UX preview: python -m UX %UX_ARGS%
%PY_CMD% -m UX %UX_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"

:end
if /i not "%UX_NO_PAUSE%"=="1" pause
endlocal
exit /b %EXIT_CODE%
