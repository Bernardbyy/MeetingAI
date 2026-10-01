# MeetingAI

Records a meeting on Windows — your microphone *and* everything you hear — then
transcribes it and writes the minutes. Everything stays on your machine: the
only network address it contacts is the local Ollama server. Built for CPU-only
inference on 16GB of RAM.

Each meeting is a plain folder under `meetings/` with `audio.mp3`,
`transcript.txt` and `minutes.md`. Delete the folder and the meeting is gone.

## Setup

You need Windows 10 or 11, about 9GB of free disk space, and internet during
setup (afterwards MeetingAI runs offline). Windows may ask once for permission
to install Git.

1. Open **PowerShell**: press the Windows key, type `PowerShell`, press Enter.
2. Copy the line below (the copy button is at its right end), paste it into
   PowerShell with a right-click, and press Enter:

```powershell
irm https://raw.githubusercontent.com/Bernardbyy/MeetingAI/main/Batch/install.bat -OutFile $env:TEMP\meetingai-install.bat; & $env:TEMP\meetingai-install.bat
```

It installs everything MeetingAI needs, downloads the models, puts the app in
`%USERPROFILE%\MeetingAI` and opens it. Allow some time: it is about 6GB in
total, mostly Ollama, PyTorch and the models. If a step fails, fix it and run the line
again; finished steps are skipped.

After that, find **MeetingAI** in the Start menu. To close it, use the
power button at the top right of the page.

### What gets installed

| What | Why | Size |
|---|---|---|
| Git | Downloads MeetingAI and its updates | 62MB |
| uv | Installs the exact Python and packages MeetingAI was tested with | 17MB |
| Microsoft Visual C++ runtime | Standard Windows files that uv and the Python packages need; most PCs already have it | 18MB |
| FFmpeg | Joins and compresses the recordings into MP3 | 245MB |
| Ollama | Runs the AI model that writes the minutes, on your machine | 1.5GB |
| Python 3.11 and packages | The app itself, speech recognition, speaker identification (PyTorch, pyannote), echo removal, Word export | 1.2GB |
| `qwen3.5:0.8b` | The model that writes the minutes | 1GB |
| Whisper `small` | The model that turns speech into text | 460MB |
| Speaker model | Tells the remote voices apart | 30MB |

The tools come from winget, Windows' own installer; the models are downloaded
once and then used offline.

## Updates

In MeetingAI, open **Settings** and press **Check for updates**. If there is
anything new, it lists the changes; **Update and restart** installs them and
opens MeetingAI again by itself. Nothing updates without you pressing it, and
updates never touch your meetings or settings.

If MeetingAI will not start, update from outside it instead: run
`Batch\update.bat` in `%USERPROFILE%\MeetingAI`.

## If something goes wrong

| What you see | What to do |
|---|---|
| Setup stops partway | Fix what it says and run the setup line again; finished steps are skipped. |
| "winget not found" | Update **App Installer** from the Microsoft Store. |
| No minutes: "Could not reach Ollama" | Start **Ollama** from the Start menu, then record again. |
| "Speakers" says identification failed | Run `Batch\after-update.bat`; it fetches anything missing. |
| The page says "can't reach this page" | Wait a few seconds and refresh; the app may still be starting. |
| Buttons do nothing after an update | Close MeetingAI, start it again, then press Ctrl+F5 in the browser. |

## Settings

Choose the transcription model, summarization model and audio devices in the
app's Settings panel. Changes apply from the next recording.

Both model lists show only what is on your machine. Setup installs Whisper
`small` and `qwen3.5:0.8b`.

**Transcription (Whisper).** Bigger is more accurate and slower. To add another
size, run this in `%USERPROFILE%\MeetingAI`:

```powershell
uv run python -c "from app import transcribe; transcribe.get_model('medium')"
```

| Whisper model | Speed on CPU | 2h meeting |
|---|---|---|
| `tiny` | ~15-20× realtime | ~7 min |
| `base` | ~10× | ~12 min |
| `small` (default) | ~4-5× | ~25-30 min |
| `medium` | ~1.5× | ~80 min |
| `large-v3` | slower than realtime | 2h+ |

**Summarization (Ollama).** Add a model with `ollama pull <name>` and it appears
in the list. The default, `qwen3.5:0.8b`, is small and fast but can get owners
or details wrong, so check the minutes before sending them. With RAM to spare
(about 4GB while it runs), `ollama pull qwen3.5:4b` writes noticeably better
minutes, more slowly.

## Speaker identification

After the minutes are written, MeetingAI works out which remote voice said what
(Speaker 1, Speaker 2, ...), in the background. It takes about as long as the
meeting, so the minutes never wait for it. When it is done, open the meeting,
press **Speakers**, play each voice, type who it is, and choose **Save & update
minutes** to have them written again with the names.

Setup downloads the model it needs (30MB) from this repository's
[release](https://github.com/Bernardbyy/MeetingAI/releases/tag/speaker-model-v1),
so no extra account is needed. After that it runs offline, with pyannote's usage
reporting switched off.

The model is [pyannote speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
by pyannoteAI, redistributed unmodified under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Project Structure

```
Batch/
  install.bat       one-shot setup: tools, code, models
  run.bat           start the app
  update.bat        update from a terminal, when the app will not start
  after-update.bat  packages and models a new version needs; run by every update
  apply-update.bat  the in-app update: close, update, restart
app/
  main.py        FastAPI routes
  session.py     one recording: capture, transcribe-as-you-go, summarize
  audio.py       WASAPI dual capture, chunk writer, ffmpeg mixdown
  echo.py        removes the speakers' echo from the mic before transcription
  transcribe.py  faster-whisper, chunk stitching, You/Them tagging
  speakers.py    tells the remote voices apart after the meeting (pyannote)
  summarize.py   Ollama prompt and minutes
  storage.py     meeting folders
  export.py      minutes as a Word document
  settings.py    settings.json
  updates.py     check for updates and update from the Settings panel
  static/        the UI: index.html, app.js, style.css, favicon.ico
pyproject.toml   dependencies
uv.lock          exact package versions
```
