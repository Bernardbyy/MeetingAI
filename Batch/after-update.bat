@echo off
rem Run by update.bat right after it pulls a new version. This file comes from
rem the new version, so a version can add the steps it needs here. Safe to run
rem again: every step skips what is already in place.
cd /d "%~dp0.."

uv sync || exit /b 1

rem Installs from before speaker identification fetch its model here.
uv run python -m app.speakers --download || exit /b 1

rem The default minutes model may have changed in this version.
for /f %%m in ('uv run python -c "from app import summarize; print(summarize.DEFAULT_MODEL)"') do set MODEL=%%m
ollama list >nul 2>&1 || (start "" /min ollama serve & timeout /t 5 >nul)
ollama pull %MODEL% || exit /b 1
exit /b 0
