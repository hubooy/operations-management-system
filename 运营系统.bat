@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Keep this CMD wrapper ASCII-only. The PowerShell controller owns all
rem localized UI so cmd.exe never has to parse UTF-8 text in a batch file.
set "SERVICE=%~dp0tools\operations-system-service.ps1"
if not exist "%SERVICE%" goto missing_service

if "%~1"=="" goto menu
if /i "%~1"=="start" goto start
if /i "%~1"=="start-bg" goto start_bg
if /i "%~1"=="stop" goto stop
if /i "%~1"=="stop-worker" goto stop_worker
if /i "%~1"=="restart" goto restart
if /i "%~1"=="restart-full" goto restart_full
if /i "%~1"=="restart-bg" goto restart_bg
if /i "%~1"=="status" goto status
if /i "%~1"=="logs" goto logs
goto invalid_option

:menu
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Menu
exit /b %ERRORLEVEL%

:start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Start -Open
exit /b %ERRORLEVEL%

:start_bg
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Start -Open -Background
exit /b %ERRORLEVEL%

:stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Stop
exit /b %ERRORLEVEL%

:stop_worker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Stop -KeepBackend
exit /b %ERRORLEVEL%

:restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Restart -KeepBackend -Open
exit /b %ERRORLEVEL%

:restart_full
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Restart -Open
exit /b %ERRORLEVEL%

:restart_bg
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Restart -KeepBackend -Open -Background
exit /b %ERRORLEVEL%

:status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Status
exit /b %ERRORLEVEL%

:logs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVICE%" -Action Logs
exit /b %ERRORLEVEL%

:missing_service
echo ERROR: lifecycle service not found: "%SERVICE%"
pause
exit /b 1

:invalid_option
echo ERROR: invalid option "%~1"
echo Available: start ^| start-bg ^| stop ^| stop-worker ^| restart ^| restart-full ^| restart-bg ^| status ^| logs
exit /b 1
