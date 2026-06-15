@echo off
setlocal
REM MO Agent launcher for Windows
REM Usage: mo.bat [options]
REM        mo.bat --init        (first-time setup)
REM        mo.bat migrate-state (migrate legacy state)

python "%~dp0mo.py" %*
