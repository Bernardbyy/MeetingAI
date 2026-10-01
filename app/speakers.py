"""Who said what on the Them side, worked out after the meeting.

pyannote community-1 groups the remote voices into Speaker 1, Speaker 2, ...
It is slow on a CPU (a 14-minute meeting took 11 minutes), so it never holds
up the minutes: they are written first, then this runs in a child process at
low priority, one meeting at a time. The mic side is always one person, You.

A person then listens to each voice and names it; the transcript shows the
names, and the minutes can be written again with them.

Files and state, per meeting folder:
    segments.json   every transcript line: start, end, text, channel, speaker
    speakers.json   what pyannote heard on the Them channel: [start, end, label]
    meta.json       speakers_state  queued | running | done | failed
                    speakers_error  why it failed
                    speaker_names   {"You": "Bernard", "Speaker 1": "Maple"}
                    minutes_state   updating | failed, while minutes are rewritten

Tested on a meeting with two remote voices: about 98% of their lines went to
the right person (a lighter engine, sherpa-onnx, managed 70%).
"""

import hashlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import storage, summarize, transcribe

# The model ships as a download on our own GitHub release, so nobody needs a
# HuggingFace account. CC BY 4.0: credited in ATTRIBUTION.txt inside the zip.
MODEL_DIR = Path(__file__).parent.parent / "models" / "speaker-diarization-community-1"
MODEL_URL = ("https://github.com/Bernardbyy/MeetingAI/releases/download/"
             "speaker-model-v1/speaker-model.zip")
MODEL_SHA256 = "2fae0f667554efdb8ef5454ec25e803fe8ac74dd997adfa7b6d30114e5269181"

# Nothing may leave the machine. pyannote reports usage to pyannote.ai unless
# told not to, and the model must come from MODEL_DIR, never the Hub.
OFFLINE_ENV = {"PYANNOTE_METRICS_ENABLED": "0", "HF_HUB_OFFLINE": "1"}

RATE = 16000
SAMPLE_SECONDS = 8
NAME_MAX = 60


# --- in the child process ------------------------------------------------------

def _them_audio(meeting_dir: Path):
    """The Them side (right channel) of the meeting audio, mono, 16 kHz."""
    import numpy as np

    audio = next((meeting_dir / n for n in ("audio.mp3", "audio.wav")
                  if (meeting_dir / n).exists()), None)
    if audio is None:
        raise RuntimeError("the meeting has no audio file")
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(audio),
         "-af", "pan=mono|c0=c1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not read the Them side: {r.stderr.decode()[:200]}")
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768


def diarize(meeting_dir: Path) -> list:
    """Run pyannote on the Them side and write speakers.json."""
    os.environ.update(OFFLINE_ENV)  # before pyannote is imported
    import torch
    from pyannote.audio import Pipeline

    if not (MODEL_DIR / "config.yaml").exists():
        raise RuntimeError(r"the speaker model is missing; run Batch\update.bat to fetch it")
    pipe = Pipeline.from_pretrained(MODEL_DIR)
    wave = torch.from_numpy(_them_audio(meeting_dir))[None]
    out = pipe({"waveform": wave, "sample_rate": RATE})
    ann = getattr(out, "speaker_diarization", out)
    turns = [[round(t.start, 2), round(t.end, 2), label]
             for t, _, label in ann.itertracks(yield_label=True)]
    (meeting_dir / "speakers.json").write_text(json.dumps(turns), encoding="utf-8")
    return turns


def download() -> None:
    """Fetch the model from our release, check it, unpack it. Skips if present.
    The only network call speaker identification ever makes, and only at setup."""
    if (MODEL_DIR / "config.yaml").exists():
        print("Speaker model already installed.")
        return
    with urllib.request.urlopen(MODEL_URL, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise SystemExit("Speaker model download was corrupted; run this again.")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(MODEL_DIR.parent)
    print("Speaker model installed.")


# --- in the app -----------------------------------------------------------------

_LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] ([^:]+): (.*)$")


def load_segments(meeting_id: str) -> list[dict]:
    """segments.json, or, for meetings recorded before it existed, the lines of
    transcript.txt with each line assumed to run until the next one starts."""
    d = storage.meeting_dir(meeting_id)
    p = d / "segments.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    segs = []
    for line in (d / "transcript.txt").read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m:
            start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            segs.append({"start": start, "end": start + 1, "text": m[5],
                         "channel": "mic" if m[4] == "You" else "sys"})
    for ch in ("mic", "sys"):
        side = [s for s in segs if s["channel"] == ch]
        for a, b in zip(side, side[1:]):
            a["end"] = max(a["start"] + 1, min(b["start"], a["start"] + 10))
    return segs


def assign(segments: list[dict], turns: list) -> list[dict]:
    """Give every Them line the voice that overlaps it most, numbered in the
    order the voices first speak. Mic lines are You and stay unlabelled."""
    order: dict[str, str] = {}
    out = []
    for s in sorted(segments, key=lambda s: s["start"]):
        s = {k: v for k, v in s.items() if k != "speaker"}
        if s["channel"] == "sys":
            overlap: dict[str, float] = {}
            for start, end, label in turns:
                o = min(s["end"], end) - max(s["start"], start)
                if o > 0:
                    overlap[label] = overlap.get(label, 0) + o
            if overlap:
                label = max(overlap, key=overlap.get)
                order.setdefault(label, f"Speaker {len(order) + 1}")
                s["speaker"] = order[label]
        out.append(s)
    return out


def _write(meeting_id: str, segments: list[dict]) -> None:
    d = storage.meeting_dir(meeting_id)
    names = storage.read_meta(meeting_id).get("speaker_names", {})
    (d / "segments.json").write_text(json.dumps(segments), encoding="utf-8")
    (d / "transcript.txt").write_text(transcribe.format_transcript(segments, names),
                                      encoding="utf-8")


def apply(meeting_id: str) -> None:
    turns = json.loads((storage.meeting_dir(meeting_id) / "speakers.json")
                       .read_text(encoding="utf-8"))
    _write(meeting_id, assign(load_segments(meeting_id), turns))


def summary(meeting_id: str) -> list[dict]:
    """One row per voice for the Speakers panel, You first."""
    names = storage.read_meta(meeting_id).get("speaker_names", {})
    rows: dict[str, dict] = {}
    for s in load_segments(meeting_id):
        label = s.get("speaker") or ("You" if s["channel"] == "mic" else None)
        if not label:
            continue
        r = rows.setdefault(label, {"label": label, "name": names.get(label, label),
                                    "lines": 0, "seconds": 0.0})
        r["lines"] += 1
        r["seconds"] += s["end"] - s["start"]
    return sorted(rows.values(), key=lambda r: (r["label"] != "You", r["label"]))


def set_names(meeting_id: str, names: dict) -> None:
    """Store the names people gave the voices and show them in the transcript.
    A blank name puts the voice back to its label."""
    clean = {}
    for label, name in names.items():
        if not isinstance(label, str) or not isinstance(name, str):
            raise ValueError("names must be text")
        name = " ".join(name.split())[:NAME_MAX]
        if name and name != label:
            clean[label] = name
    storage.update_meta(meeting_id, speaker_names=clean)
    _write(meeting_id, load_segments(meeting_id))


def sample(meeting_id: str, label: str) -> bytes:
    """A few seconds of one voice as MP3: its longest line, from its own side."""
    d = storage.meeting_dir(meeting_id)
    lines = [s for s in load_segments(meeting_id)
             if (s.get("speaker") or ("You" if s["channel"] == "mic" else None)) == label]
    if not lines:
        raise KeyError(label)
    s = max(lines, key=lambda s: s["end"] - s["start"])
    audio = next(d / n for n in ("audio.mp3", "audio.wav") if (d / n).exists())
    side = "c0" if label == "You" else "c1"
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{s['start']:.2f}",
         "-t", f"{min(SAMPLE_SECONDS, max(2, s['end'] - s['start'])):.2f}",
         "-i", str(audio), "-af", f"pan=mono|c0={side}", "-f", "mp3", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:200])
    return r.stdout


def speaker_note(meeting_id: str) -> str | None:
    """How the minutes should treat names, once any voice has one."""
    meta = storage.read_meta(meeting_id)
    if meta.get("speakers_state") != "done":
        return None
    return summarize.NAMED_SPEAKERS


# --- background work --------------------------------------------------------------

_jobs: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None


def _run(meeting_id: str) -> None:
    d = storage.meeting_dir(meeting_id)
    storage.update_meta(meeting_id, speakers_state="running", speakers_error=None)
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)  # Windows only
    r = subprocess.run([sys.executable, "-m", "app.speakers", str(d.resolve())],
                       env={**os.environ, **OFFLINE_ENV}, capture_output=True, text=True,
                       creationflags=flags, cwd=Path(__file__).parent.parent)
    if r.returncode != 0:
        why = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
        storage.update_meta(meeting_id, speakers_state="failed", speakers_error=why[:300])
        return
    apply(meeting_id)
    storage.update_meta(meeting_id, speakers_state="done")


def _loop() -> None:
    while True:
        meeting_id = _jobs.get()
        try:
            _run(meeting_id)
        except Exception as e:  # a broken meeting must not stop the queue
            try:
                storage.update_meta(meeting_id, speakers_state="failed", speakers_error=str(e)[:300])
            except Exception:
                pass


def enqueue(meeting_id: str) -> None:
    global _worker
    storage.update_meta(meeting_id, speakers_state="queued", speakers_error=None)
    _jobs.put(meeting_id)
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, name="speakers", daemon=True)
        _worker.start()


def resume() -> None:
    """Meetings still queued or running when the app last closed start again."""
    for m in sorted(storage.list_meetings(), key=lambda m: m["id"]):
        if m.get("speakers_state") in ("queued", "running"):
            enqueue(m["id"])
        if m.get("minutes_state") == "updating":
            storage.update_meta(m["id"], minutes_state="failed",
                                minutes_error="the app closed while the minutes were being updated")


def update_minutes(meeting_id: str, model: str) -> None:
    """Write the minutes again from the named transcript, in the background."""
    storage.update_meta(meeting_id, minutes_state="updating", minutes_error=None)

    def work():
        d = storage.meeting_dir(meeting_id)
        try:
            text = (d / "transcript.txt").read_text(encoding="utf-8")
            minutes = summarize.summarize(text, model=model,
                                          speakers=speaker_note(meeting_id))
            (d / "minutes.md").write_text(minutes, encoding="utf-8")
            storage.update_meta(meeting_id, minutes_state=None, minutes_edited_at=None,
                                minutes_model=model)
        except Exception as e:
            storage.update_meta(meeting_id, minutes_state="failed", minutes_error=str(e)[:300])

    threading.Thread(target=work, name="minutes", daemon=True).start()


if __name__ == "__main__":
    if sys.argv[1:] == ["--download"]:
        download()
    else:
        diarize(Path(sys.argv[1]))
