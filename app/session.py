"""One recording session: capture, transcribe-as-you-go, then summarize.

The transcription worker consumes chunks while the meeting is still running, so
pressing Stop usually leaves only the last chunk or two to process.

Chunk offsets accumulate per channel from real durations, in queue order. The
two channels are independent clocks and are only merged when the transcript is
formatted.

Each mic chunk waits for the speaker chunk recorded over the same minute (the
k-th of each), so echo.py can remove the speakers' sound from it first.
"""

import threading
import time
from pathlib import Path

from . import audio, echo, storage, summarize, transcribe

# Whisper on an empty or near-empty wav is a waste; 44 bytes is a bare header.
MIN_CHUNK_BYTES = 1024


class Session:
    def __init__(self, meeting_id: str, model_name: str | None = None,
                 chunk_seconds: int = audio.CHUNK_SECONDS, recorder=None,
                 llm_model: str | None = None, mic_index: int | None = None,
                 loopback_index: int | None = None):
        self.meeting_id = meeting_id
        self.dir = storage.meeting_dir(meeting_id)
        self.model_name = model_name or transcribe.DEFAULT_MODEL
        self.llm_model = llm_model or summarize.DEFAULT_MODEL
        self.recorder = recorder or audio.Recorder(
            self.dir, chunk_seconds=chunk_seconds,
            mic_index=mic_index, loopback_index=loopback_index,
        )
        self.stage = "idle"
        self.stage_since = time.time()
        self.started_at = 0.0
        self.devices: dict = {}
        self.segments: list[dict] = []
        self.transcribed = 0
        self.errors: list[str] = []
        self._offsets = {"mic": 0.0, "sys": 0.0}
        self._worker: threading.Thread | None = None
        # Echo cancellation: mic chunks wait here for their speaker reference.
        self._mic_waiting: list[Path] = []
        self._refs: list[Path] = []
        self._mics_done = 0
        self._canceller = None
        self._echo_off = False
        # Whisper guesses the language per chunk, and with the echo gone it can
        # misread an accent (English heard as Malay). The first Them chunk sets
        # it for the meeting: that side is never processed, so the guess is sound.
        # ponytail: one language per meeting; code-switching meetings lose out.
        self.language: str | None = None
        # Long meetings are summarized in batches; the UI counts them.
        self.batch = 0
        self.batches = 0

    # --- lifecycle -----------------------------------------------------

    def _set_stage(self, stage: str):
        self.stage = stage
        self.stage_since = time.time()

    def start(self) -> dict:
        devices = self.recorder.start()
        self.started_at = time.time()
        self.devices = devices
        self._set_stage("recording")
        self._worker = threading.Thread(target=self._consume, name="transcriber",
                                        daemon=True)
        self._worker.start()
        return devices

    def stop(self) -> dict:
        """Stop capture, drain the queue, write transcript.txt and audio.wav."""
        duration = int(time.time() - self.started_at)
        self._set_stage("finishing")
        chunks = self.recorder.stop_capture()

        self._set_stage("transcribing")
        self.recorder.closed.put(None)  # sentinel: drain what is left
        if self._worker:
            self._worker.join()

        text = transcribe.format_transcript(self.segments)
        (self.dir / "transcript.txt").write_text(text, encoding="utf-8")

        # Everything from here down is best-effort: the transcript is already on
        # disk, so no later failure may take the meeting with it.
        self._set_stage("saving")
        audio_path = None
        try:
            audio_path = audio.build_audio(self.dir, chunks.get("mic", []),
                                           chunks.get("sys", []))
        except Exception as e:  # ffmpeg missing, killed, or fed a torn chunk
            self.errors.append(f"could not build audio.wav: {e}")

        self._set_stage("summarizing")
        minutes = ""
        try:
            minutes = summarize.summarize(text, model=self.llm_model,
                                          progress=self._on_batch)
            (self.dir / "minutes.md").write_text(minutes, encoding="utf-8")
        except summarize.SummarizeError as e:
            self.errors.append(str(e))

        self._set_stage("idle")
        self.errors += self.recorder.errors
        return {
            "meeting_id": self.meeting_id,
            "duration_sec": duration,
            "audio": audio_path.name if audio_path else None,
            "transcript": text,
            "minutes": minutes,
            "errors": self.errors,
        }

    # --- worker --------------------------------------------------------

    def _consume(self):
        q = self.recorder.closed
        while True:
            item = q.get()
            if item is None:
                break
            path = Path(item["path"])
            if item["channel"] == "sys":
                self._refs.append(path)
                self._safely(path, "sys")
            else:
                self._mic_waiting.append(path)
            self._drain_mics()
        # Speaker chunks ran out (loopback failed or stopped short): the rest of
        # the mic goes through as recorded.
        self._drain_mics(final=True)

    def _drain_mics(self, final: bool = False):
        """Transcribe waiting mic chunks, in order, once their reference exists."""
        while self._mic_waiting and (final or self._mics_done < len(self._refs)):
            mic = self._mic_waiting.pop(0)
            ref = self._refs[self._mics_done] if self._mics_done < len(self._refs) else None
            self._mics_done += 1
            self._safely(mic, "mic", ref)

    def _safely(self, path: Path, channel: str, ref: Path | None = None):
        try:
            self._transcribe(path, channel, ref)
        except Exception as e:  # one bad chunk must not lose the meeting
            self.errors.append(f"{path.name}: {e}")
        finally:
            self.transcribed += 1

    def _on_batch(self, done: int, total: int):
        self.batch, self.batches = done, total

    def _without_echo(self, mic: Path, ref: Path | None) -> Path:
        """The mic chunk with the speakers' sound removed, or as recorded if
        that is not possible. A failure turns cancellation off for the meeting
        rather than risk a half-adapted filter on the chunks after it."""
        if self._echo_off or ref is None or not ref.exists() \
                or ref.stat().st_size < MIN_CHUNK_BYTES:
            return mic
        try:
            if self._canceller is None:
                self._canceller = echo.Canceller()
            return self._canceller.clean(mic, ref)
        except Exception as e:
            self._echo_off = True
            self.errors.append(f"echo cancellation off: {e}")
            return mic

    def _transcribe(self, path: Path, channel: str, ref: Path | None = None):
        if not path.exists() or path.stat().st_size < MIN_CHUNK_BYTES:
            return
        offset = self._offsets[channel]
        self._offsets[channel] = offset + transcribe.wav_duration(path)
        source = self._without_echo(path, ref) if channel == "mic" else path
        try:
            segs = transcribe.transcribe_chunk(
                source, channel, offset, model_name=self.model_name,
                language=self.language,
            )
            self.segments += segs
            if channel == "sys" and self.language is None and segs:
                self.language = segs[0].get("language")
        finally:
            if source != path:
                source.unlink(missing_ok=True)

    # --- reporting -----------------------------------------------------

    def status(self) -> dict:
        elapsed = int(time.time() - self.started_at) if self.started_at else 0
        return {
            "stage": self.stage,
            "meeting_id": self.meeting_id,
            "elapsed_sec": elapsed if self.stage == "recording" else 0,
            "stage_sec": int(time.time() - self.stage_since),
            "closed": self.recorder.closed_count,
            "transcribed": self.transcribed,
            "batch": self.batch,
            "batches": self.batches,
            "devices": self.devices,
            "models": {"whisper": self.model_name, "llm": self.llm_model},
            "errors": self.errors + self.recorder.errors,
        }
