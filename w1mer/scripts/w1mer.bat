@echo off
rem W1MER CLI launcher (Windows). Call: w1mer <command> [args...]
setlocal
set "SCRIPT_DIR=%~dp0"
python "%SCRIPT_DIR%w1mer.py" %*
exit /b %ERRORLEVEL%
