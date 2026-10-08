"""Chunk -> tagged, time-ordered transcript.

Each side is transcribed independently, then interleaved by timestamp:

    [00:01:12] You: are we shipping Friday
    [00:01:20] Them: Friday works

Timestamps are absolute within the meeting. Chunk offsets come from the actual
wav durations, not chunk_index * 60, because the last chunk is short and a
dropped buffer makes the rest drift.
"""

import os
import wave
from pathlib import Path

# Hugging Face's cache stores model files as symlinks, which ordinary Windows
# accounts may not create. Its own support check is meant to fall back to
# copies, but it can race its parallel downloads: on a colleague's laptop the
# Whisper download died with "WinError 1314: A required privilege is not held".
# Plain copies always work. Must be set before huggingface_hub is imported.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

DEFAULT_MODEL = os.environ.get("MEETINGAI_WHISPER_MODEL", "small")

# Capture label -> how it reads in the transcript.
SPEAKER = {"mic": "You", "sys": "Them"}

_models = {}


def get_model(name: str = DEFAULT_MODEL):
    """Cached WhisperModel. Imported lazily so the rest of this module and its
    tests do not need faster-whisper installed."""
    if name not in _models:
        from faster_whisper import WhisperModel

        _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _models[name]


def downloaded_models(names, cache_dir=None) -> list[str]:
    """The Whisper models among `names` already in the local cache.

    Looks on disk only: never downloads, never touches the network. Uses
    faster-whisper's own resolver, so it agrees with what WhisperModel loads.
    """
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        return []
    found = []
    for name in names:
        try:
            download_model(name, local_files_only=True, cache_dir=cache_dir)
            found.append(name)
        except Exception:  # not cached, or a half-written cache entry
            pass
    return found


def wav_duration(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def chunk_offsets(paths: list[Path]) -> list[float]:
    """Start time of each chunk, from real durations."""
    offsets, t = [], 0.0
    for p in paths:
        offsets.append(t)
        t += wav_duration(p)
    return offsets


def transcribe_chunk(path, channel: str, offset: float = 0.0,
                     model_name: str = DEFAULT_MODEL, language: str | None = None,
                     _model=None) -> list[dict]:
    """One chunk -> segments with meeting-absolute timestamps."""
    model = _model if _model is not None else get_model(model_name)
    # ponytail: greedy decode + VAD. beam_size=5 is roughly 3x slower on CPU for
    # a small accuracy gain, and VAD skips the silence that dominates the mic
    # channel. Raise beam_size if transcripts read badly.
    segments, info = model.transcribe(
        str(path), beam_size=1, vad_filter=True, language=language,
        word_timestamps=True,
    )
    lang = getattr(info, "language", None)
    return [
        {"start": start + offset, "end": end + offset, "text": text,
         "channel": channel, "language": lang}
        for seg in segments
        for start, end, text in split_on_pauses(seg)
        if text
    ]


# Each side is transcribed on its own, so while the other side talks this
# channel is silent, and Whisper can glue the words before and after that
# silence into one segment stamped with the earlier time. In one meeting a
# reply said at 8:15 came out as 8:00, ahead of the question it answered.
# Splitting at pauses gives each piece its real start time.
PAUSE_SECONDS = 1.0


def split_on_pauses(seg) -> list[tuple[float, float, str]]:
    """(start, end, text) pieces of a segment, cut wherever the speaker paused."""
    words = getattr(seg, "words", None)
    if not words:
        return [(seg.start, seg.end, seg.text.strip())]
    pieces, current = [], [words[0]]
    for prev, w in zip(words, words[1:]):
        if w.start - prev.end > PAUSE_SECONDS:
            pieces.append(current)
            current = []
        current.append(w)
    pieces.append(current)
    return [(p[0].start, p[-1].end, "".join(w.word for w in p).strip()) for p in pieces]


def hhmmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_transcript(segments: list[dict], names: dict | None = None) -> str:
    """Interleave both channels by time. Stable: on a tie, you come first.

    A line reads as its voice ("Speaker 2", once speakers.py has told the Them
    voices apart, else You / Them), shown by the name a person gave it if any.
    """
    names = names or {}
    ordered = sorted(segments, key=lambda s: (s["start"], s["channel"] != "mic"))

    def who(s):
        label = s.get("speaker") or SPEAKER.get(s["channel"], s["channel"])
        return names.get(label, label)

    return "\n".join(f"[{hhmmss(s['start'])}] {who(s)}: {s['text']}" for s in ordered)


def transcribe_meeting(meeting_dir, model_name: str = DEFAULT_MODEL,
                       progress=None, _model=None) -> str:
    """Transcribe every chunk in a meeting folder and write transcript.txt."""
    meeting_dir = Path(meeting_dir)
    chunks_dir = meeting_dir / "chunks"

    work = []
    for channel in ("mic", "sys"):
        paths = sorted(chunks_dir.glob(f"{channel}_*.wav"))
        work += list(zip(paths, chunk_offsets(paths), [channel] * len(paths)))

    segments = []
    for i, (path, offset, channel) in enumerate(work, 1):
        if progress:
            progress(i, len(work))
        segments += transcribe_chunk(path, channel, offset,
                                     model_name=model_name, _model=_model)

    text = format_transcript(segments)
    (meeting_dir / "transcript.txt").write_text(text, encoding="utf-8")
    return text
