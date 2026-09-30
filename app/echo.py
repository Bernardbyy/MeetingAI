"""Echo cancellation: take the speakers' sound back out of the mic.

On laptop speakers the mic re-hears everyone else, so their words were
transcribed twice: once as Them and again, as echo, as You. The loopback chunk
is exactly what the speakers played, which is the reference WebRTC's echo
canceller needs: it learns how the room changes that sound and subtracts it
from the mic. With headphones there is no echo and the mic passes through.

Only the audio sent to Whisper is cleaned; audio.mp3 keeps the raw recording.
"""

import wave
from pathlib import Path

import numpy as np

RATE = 48000
FRAME = RATE // 100  # the canceller takes exactly 10 ms per call

# The mic and the speakers run on separate hardware clocks, so their recordings
# slip against each other: in one 14-minute meeting the echo went from arriving
# 18 ms after the speaker sound to 24 ms *before* it, and a canceller cannot
# remove echo that seems to come before its source. Each chunk measures the
# slip and delays the mic so the echo lands LEAD_MS after the reference.
# Measured on that meeting: minutes 8-13 went from 2-7 dB of echo removed to 21-46.
LEAD_MS = 80
MAX_LAG_MS = 500
MIN_ECHO = 0.05  # below this correlation there is no echo to line up (headphones)


def echo_lag_ms(mic: np.ndarray, ref: np.ndarray) -> tuple[float, float]:
    """How many ms after the speaker sound its echo reaches the mic (negative if
    the recordings have slipped), and how clearly the echo shows at all."""
    m, r = mic.astype(np.float64), ref[:len(mic)].astype(np.float64)
    n = 1 << int(np.ceil(np.log2(2 * len(m))))
    g = np.fft.rfft(m, n) * np.conj(np.fft.rfft(r, n))
    cc = np.fft.irfft(g / (np.abs(g) + 1e-9), n)  # GCC-PHAT: sharp peak at the lag
    w = RATE * MAX_LAG_MS // 1000
    cc = np.concatenate([cc[-w:], cc[:w + 1]])
    k = int(np.argmax(cc))
    return (k - w) * 1000 / RATE, float(cc[k])


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
        self._shift = 0  # samples the mic is delayed by; kept while there is no echo

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
        mic = mic[:n]

        lag, strength = echo_lag_ms(mic, ref)
        if strength > MIN_ECHO:
            self._shift = max(0, round((LEAD_MS - lag) * RATE / 1000))
        s0 = self._shift
        mic = np.concatenate([np.zeros(s0, dtype=np.int16), mic])[:n]  # delay the mic
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
        # Undo the delay so Whisper's timestamps still match the meeting clock.
        out = np.concatenate([out[s0:], np.zeros(s0, dtype=np.int16)])

        dest = Path(mic_path).with_suffix(".clean.wav")
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(out.tobytes())
        return dest
