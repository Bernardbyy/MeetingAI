@echo off
setlocal
rem One-shot setup: tools, code, models, then launch. Safe to run again:
rem every step skips itself when already done.

set "DIR=%USERPROFILE%\MeetingAI"
rem Run from inside a clone: use that clone instead of making a second one.
if exist "%~dp0..\.git" set "DIR=%~dp0.."
set "REPO=https://github.com/Bernardbyy/MeetingAI"
rem --source winget: the Microsoft Store source is often broken or blocked on work
rem laptops, and winget then refuses to install even what it found elsewhere.
set WG=winget install -e --source winget --silent --accept-package-agreements --accept-source-agreements --id

where winget >nul 2>&1 || (echo winget not found. Update "App Installer" from the Microsoft Store. & goto :error)

rem A first install needs about 9GB; say so now rather than fail halfway.
if not exist "%DIR%" (
    for /f %%g in ('powershell -NoProfile -Command "[math]::Floor((Get-PSDrive $env:USERPROFILE[0]).Free / 1GB)"') do set FREEGB=%%g
)
if defined FREEGB if %FREEGB% LSS 10 (
    echo Only %FREEGB%GB free on this drive; MeetingAI needs about 9GB. Free some space first.
    goto :error
)

echo [1/6] Installing tools, skipping any already installed...
where git    >nul 2>&1 || %WG% Git.Git
where uv     >nul 2>&1 || %WG% astral-sh.uv
where ffmpeg >nul 2>&1 || %WG% Gyan.FFmpeg
where ollama >nul 2>&1 || %WG% Ollama.Ollama
rem An Ollama installed long ago cannot run the model or switch thinking off.
rem Quietly bring it up to date; nothing happens if it already is.
winget upgrade -e --source winget --silent --accept-package-agreements --accept-source-agreements --id Ollama.Ollama >nul 2>&1
rem New installs are not on this window's PATH yet; reread it from the registry.
call :refresh_path
for %%t in (git uv ffmpeg ollama) do where %%t >nul 2>&1 || (echo %%t did not install. & goto :error)

echo [2/6] Getting the code...
if exist "%DIR%\.git" (echo Already at %DIR%) else (git clone --quiet %REPO% "%DIR%" || goto :error)
cd /d "%DIR%"

echo [3/6] Downloading the summarization model, 1GB the first time...
ollama list >nul 2>&1 || (start "" /min ollama serve & timeout /t 5 >nul)
ollama pull qwen3.5:0.8b || goto :error

echo [4/6] Installing Python and packages...
uv sync || goto :error

echo [5/6] Downloading the speaker model, 30MB the first time...
uv run python -m app.speakers --download || goto :error

echo [6/6] Downloading the transcription model, ~460MB the first time...
rem Done here so the first recording does not stall on it. Hugging Face's
rem token and symlink warnings are harmless and only alarm people.
set HF_HUB_VERBOSITY=error
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
uv run python -c "from app import transcribe; transcribe.get_model()" || goto :error

echo.
echo Setup complete. Starting MeetingAI...
call Batch\run.bat
exit /b

:refresh_path
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p"
exit /b

:error
echo.
echo Setup stopped at the step above. Fix it and run this again; finished steps are skipped.
pause
exit /b 1
