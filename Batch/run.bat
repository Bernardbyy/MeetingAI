@echo off
rem Scripts live in Batch\; everything else runs from the project root.
cd /d "%~dp0.."

rem Already running (opened twice): just show it rather than fail on the port.
netstat -ano -p tcp | findstr /r /c:":8756 .*LISTENING" >nul && (
    start "" http://127.0.0.1:8756
    goto :eof
)

rem No git pull here on purpose: launching must never run code the user has not
rem chosen to install. Updates are update.bat, run by hand.
echo Syncing dependencies...
uv sync || goto :error

rem Start menu shortcut, so "MeetingAI" is findable from Windows search.
rem Rewritten every launch so it follows the folder if it is moved.
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\MeetingAI.lnk\"); $s.TargetPath='%~f0'; $s.WorkingDirectory='%CD%'; $s.IconLocation='%CD%\app\static\favicon.ico'; $s.Save()" >nul 2>&1

rem Open the browser once the app answers, not after a fixed wait: a slow first
rem start would otherwise land on "can't reach this page". Gives up after 60 s.
start "" /b powershell -NoProfile -Command "for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8756/api/status | Out-Null; Start-Process http://127.0.0.1:8756; break } catch { Start-Sleep 1 } }"

uv run uvicorn app.main:app --port 8756
goto :eof

:error
echo.
echo Setup failed. Is uv installed?  https://docs.astral.sh/uv/
pause
