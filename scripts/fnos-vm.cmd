@echo off
rem Launcher for the fnos-vm skill. Exit codes must match the CLI contract:
rem 0 success, 1 business/safety/network failure, 2 argument error, 3 auth failure.
rem Use goto instead of if(...) blocks: %ERRORLEVEL% inside a block is expanded
rem when the block is parsed, so `exit /b %ERRORLEVEL%` there always returned the
rem errorlevel from before python ran.
setlocal EnableExtensions
set "SCRIPT=%~dp0fnos-vm.py"

if defined FNOS_VM_PYTHON goto run_env
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" goto run_bundled
where py >nul 2>nul
if not errorlevel 1 goto run_py
where python3 >nul 2>nul
if not errorlevel 1 goto run_python3
where python >nul 2>nul
if not errorlevel 1 goto run_python

echo fnos-vm: Python 3.9+ was not found. Set FNOS_VM_PYTHON to python.exe. 1>&2
exit /b 2

:run_env
"%FNOS_VM_PYTHON%" "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_bundled
"%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_py
py -3 "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_python3
python3 "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_python
python "%SCRIPT%" %*
exit /b %ERRORLEVEL%
