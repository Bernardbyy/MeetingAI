@echo off
cd /d "%~dp0.."

rem Show what is new, then install it only if the user says yes.
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never

rem The running app holds its packages open, so updating them under it fails
rem halfway: new code, old packages. Stop before touching anything.
netstat -ano -p tcp | findstr /r /c:":8756 .*LISTENING" >nul && (
    echo MeetingAI is running. Close its window, then run this again.
    pause
    goto :eof
)

echo Checking for updates...
git fetch --quiet || goto :error

for /f %%n in ('git rev-list --count HEAD..@{u}') do set NEW=%%n
if "%NEW%"=="0" (
    echo Already up to date.
    pause
    goto :eof
)

echo.
echo %NEW% new change(s):
git log --oneline --no-decorate HEAD..@{u}
echo.
echo Full details: git diff HEAD..@{u}
echo.
choice /c YN /m "Install these updates"
if errorlevel 2 goto :eof

git pull --ff-only --quiet || goto :error
uv sync || goto :installing
rem Installs that predate speaker identification fetch its model here; a no-op after.
uv run python -m app.speakers --download || goto :installing
echo.
echo Updated. If MeetingAI is open, close its window and start it again.
pause
goto :eof

:error
echo.
echo Update failed. Offline, or you have local edits? Nothing was changed.
pause
goto :eof

:installing
echo.
echo The new version downloaded but its packages did not install. Check your
echo internet connection and run this again; it carries on from here.
pause
