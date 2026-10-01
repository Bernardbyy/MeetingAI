@echo off
rem Run by MeetingAI's "Update and restart" button, from a copy in %TEMP%, so
rem git pull can replace the original freely. %1 = the MeetingAI folder,
rem %2 = the port the app was running on.
title Updating MeetingAI
cd /d "%~1"
set "PORT=%~2"
if not defined PORT set PORT=8756
echo Updating MeetingAI. Please wait: this window closes by itself when done.
echo.

rem The app is closing itself; wait until its files are free (up to a minute).
for /l %%i in (1,1,60) do (
    netstat -ano -p tcp | findstr /r /c:":%PORT% .*LISTENING" >nul || goto :closed
    timeout /t 1 /nobreak >nul
)
echo MeetingAI did not close. Close its window, then run Batch\update.bat.
pause
exit /b 1

:closed
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never
git pull --ff-only --quiet || goto :error
call Batch\after-update.bat || goto :error

echo.
echo Updated. Starting MeetingAI again...
rem The page that asked for the update reloads itself; no second browser tab.
set MEETINGAI_NO_BROWSER=1
start "MeetingAI" cmd /c Batch\run.bat
exit /b 0

:error
echo.
echo The update did not finish. Check your internet connection, then run
echo Batch\update.bat to try again.
pause
exit /b 1
