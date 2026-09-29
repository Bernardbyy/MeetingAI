"""Echo cancellation: take the speakers' sound back out of the mic.

On laptop speakers the mic re-hears everyone else, so their words were
transcribed twice: once as Them and again, as echo, as You. The loopback chunk
is exactly what the speakers played, which is the reference WebRTC's echo
canceller needs: it learns how the room changes that sound and subtracts it
from the mic. With headphones there is no echo and the mic passes through.

Only the audio sent to Whisper is cleaned; audio.wav stays the raw recording.
"""

import wave
from pathlib import Path

import numpy as np

RATE = 48000
FRAME = RATE // 100  # the canceller takes exactly 10 ms per call


def _read(path) -> tuple[np.ndarray, int]:
    """Mono int16 samples and the file's sample rate."""
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        a = a.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
        return a, w.getframerate()


class Canceller:
    """One per meeting, fed chunk pairs in order: the filter keeps adapting to
    the room from one chunk to the next instead of relearning it each minute."""

    def __init__(self):
        from livekit import rtc  # imported here so nothing else pays for it

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True, noise_suppression=True, high_pass_filter=True)

    def _at_rate(self, a: np.ndarray, rate: int) -> np.ndarray:
        if rate == RATE:
            return a
        r = self._rtc.AudioResampler(rate, RATE, num_channels=1)
        frames = r.push(bytearray(a.tobytes())) + r.flush()
        return np.concatenate([np.frombuffer(f.data, dtype=np.int16) for f in frames])

    def clean(self, mic_path, ref_path) -> Path:
        """Write the mic chunk minus the echo of ref_path; return the new file."""
        mic = self._at_rate(*_read(mic_path))
        ref = self._at_rate(*_read(ref_path))
        n = len(mic) // FRAME * FRAME  # ponytail: drops the last <10 ms
        ref = np.pad(ref[:n], (0, max(0, n - len(ref))))
        out = np.empty(n, dtype=np.int16)

        rtc = self._rtc
        for s in range(0, n, FRAME):
            far = rtc.AudioFrame(ref[s:s + FRAME].tobytes(), RATE, 1, FRAME)
            near = rtc.AudioFrame(mic[s:s + FRAME].tobytes(), RATE, 1, FRAME)
            self._apm.process_reverse_stream(far)
            # Both chunks were captured over the same seconds, so no known lag;
            # the canceller estimates the acoustic delay itself.
            self._apm.set_stream_delay_ms(0)
            self._apm.process_stream(near)
            out[s:s + FRAME] = np.frombuffer(near.data, dtype=np.int16)

        dest = Path(mic_path).with_suffix(".clean.wav")
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(out.tobytes())
        return dest
