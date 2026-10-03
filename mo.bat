@echo off
setlocal
REM MO Agent launcher for Windows
REM Usage: mo.bat [options]
REM        mo.bat --init        (first-time setup)

python "%~dp0mo.py" %*
