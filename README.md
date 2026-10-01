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
to install Git. Paste this into PowerShell:

```powershell
irm https://raw.githubusercontent.com/Bernardbyy/MeetingAI/main/Batch/install.bat -OutFile $env:TEMP\meetingai-install.bat; & $env:TEMP\meetingai-install.bat
```

It installs everything MeetingAI needs, downloads the models, puts the app in
`%USERPROFILE%\MeetingAI` and opens it. Allow some time: it is about 8.5GB in
total, mostly Ollama, PyTorch and the models. If a step fails, fix it and run the line
again; finished steps are skipped.

After that, find **MeetingAI** in the Start menu.

## Updates

Close MeetingAI, then run `Batch\update.bat`. It lists what changed and asks
before installing anything. Starting the app never updates on its own, and
updates never touch your meetings or settings.

## If something goes wrong

| What you see | What to do |
|---|---|
| Setup stops partway | Fix what it says and run the setup line again; finished steps are skipped. |
| "winget not found" | Update **App Installer** from the Microsoft Store. |
| No minutes: "Could not reach Ollama" | Start **Ollama** from the Start menu, then record again. |
| "Speakers" says identification failed | Run `Batch\update.bat`; it fetches anything missing. |
| The page says "can't reach this page" | Wait a few seconds and refresh; the app may still be starting. |
| Buttons do nothing after an update | Close MeetingAI, start it again, then press Ctrl+F5 in the browser. |

## Settings

Choose the transcription model, summarization model and audio devices in the
app's Settings panel. Changes apply from the next recording.

Both model lists show only what is on your machine. Setup installs Whisper
`small` and `qwen3.5:4b`.

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
in the list. Expect several minutes of summarizing after Stop; a smaller model
is faster but writes weaker minutes.

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
  install.bat    one-shot setup: tools, code, models
  run.bat        start the app
  update.bat     update to the latest version
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
  static/        the UI: index.html, app.js, style.css, favicon.ico
pyproject.toml   dependencies
uv.lock          exact package versions
```
