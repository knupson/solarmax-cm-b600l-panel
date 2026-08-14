@echo off
rem Double-click this. It runs install.ps1.
rem
rem A .ps1 is not executable by double-click -- Windows opens it in Notepad -- and
rem the default execution policy on Windows client refuses to run it at all. A
rem release downloaded from GitHub also carries Mark-of-the-Web, which makes even a
rem RemoteSigned machine refuse it as unsigned. -ExecutionPolicy Bypass covers all
rem three for this one process only; nothing on the machine is changed.
rem
rem Any arguments are passed straight through:  install.cmd -Check

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set CODIGO=%ERRORLEVEL%

rem Double-clicked, the console closes the moment this ends and takes every message
rem with it. Pause only in that case: from a terminal, or in CI, it must not block.
echo %CMDCMDLINE% | find /i "%~0" >nul
if not errorlevel 1 (
    echo.
    pause
)
exit /b %CODIGO%
