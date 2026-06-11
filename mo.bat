@echo off
setlocal
REM MO Agent launcher for Windows
REM Usage: mo.bat [options]
REM        mo.bat --init        (first-time setup)
REM        mo.bat migrate-state (migrate legacy state)
REM
REM All sessions now run with tracing and validation via mo_trace.py serve.
REM Traces are saved to memory/traces/.

REM Launch monitor window in background (off by default, set MO_MONITOR=1 to enable)
if "%MO_MONITOR%"=="1" (
    start "MO Monitor" python "%~dp0mo_monitor.py"
)

python "%~dp0mo_trace.py" serve %*
