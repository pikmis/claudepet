"""Listens to what the computer is playing (WASAPI loopback on Windows, the
PulseAudio/PipeWire sink monitor on Linux) and turns it into a few simple events
for the pet: sudden loud sounds, music on/off and beats."""
from __future__ import annotations

import math
import sys
import threading
import time
import warnings
from collections import deque

import numpy as np

SAMPLE_RATE = 22_050
HOP = SAMPLE_RATE // 50  # 20 ms
HOPS_PER_SEC = 50

SILENCE_DB = -50.0
LOUD_DB = -18.0
LOUD_JUMP_DB = 15.0
LOUD_COOLDOWN = 2 * HOPS_PER_SEC
# A loud sound must come out of relative quiet: nothing this close to it in the
# last 1.5 s. Keeps speech, which pauses only briefly, from startling Clawd.
LOUD_QUIET_BEFORE = 75
LOUD_ABOVE_RECENT_DB = 20.0
BACKGROUND_SECS = 3.0
BASS_CUTOFF_HZ = 150.0
BEAT_RATIO = 1.6
BEAT_MIN_GAP = 13  # ~260 ms, i.e. up to ~230 BPM

# Music vs speech, judged on the last 6 s every 0.5 s. Thresholds were tuned on
# real speech (conversations, voice messages, talking over background music),
# several music genres and white noise.
WINDOW = 6 * HOPS_PER_SEC
JUDGE_EVERY = HOPS_PER_SEC // 2
MIN_ACTIVE = 0.9
# Share of 20 ms hops quieter than half their second's average: speech
# keeps dropping between syllables and words (0.3-0.5), music doesn't (< 0.1).
MAX_LOW_ENERGY = 0.2
# Loudness envelope std/mean over the window: speech ~1, music ~0.2-0.4.
MAX_DEPTH = 0.5
# Spectral flatness: 0 for tones, ~0.6 for white noise (rain, fans, hiss).
MAX_FLATNESS = 0.25
ON_AFTER = 6
OFF_AFTER = 6

FFT_SIZE = 512


def to_db(rms: float) -> float:
    return 20.0 * math.log10(rms + 1e-9)


class Analyzer:
    """Feed mono samples at SAMPLE_RATE to `push`; events are passed to `emit` as they occur:
    {"kind": "loud", "db"}, {"kind": "music", "playing"} and {"kind": "beat", "strength"}."""

    def __init__(self) -> None:
        dt = 1.0 / SAMPLE_RATE
        rc = 1.0 / (2.0 * math.pi * BASS_CUTOFF_HZ)
        self.pending = np.zeros(0, dtype=np.float32)
        self.lowpass = 0.0
        self.lowpass_alpha = dt / (rc + dt)
        self.background = 0.0
        self.bass_history: deque[float] = deque(maxlen=HOPS_PER_SEC)
        self.window_fn = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(HOP) / (HOP - 1))
        # Per-hop (rms, spectral flatness) for the last WINDOW hops.
        self.history: deque[tuple[float, float]] = deque(maxlen=WINDOW)
        self.since_judge = 0
        self.streak = 0
        self.music = False
        self.since_beat = BEAT_MIN_GAP
        self.since_loud = LOUD_COOLDOWN

    def is_music(self) -> bool:
        return self.music

    def push(self, samples, emit) -> None:
        self.pending = np.concatenate((self.pending, np.asarray(samples, dtype=np.float32)))
        n = len(self.pending) // HOP
        for i in range(n):
            self._hop(self.pending[i * HOP : (i + 1) * HOP], emit)
        self.pending = self.pending[n * HOP :]

    def _flatness(self, hop: np.ndarray) -> float:
        """Spectral flatness of a hop over ~86 Hz..8.6 kHz."""
        spectrum = np.fft.rfft(hop * self.window_fn, n=FFT_SIZE)[2:200]
        power = spectrum.real**2 + spectrum.imag**2 + 1e-12
        return float(np.exp(np.log(power).mean()) / power.mean())

    def _sounds_like_music(self) -> bool:
        """Whether the last WINDOW hops sound like music rather than speech or noise."""
        history = np.array(self.history, dtype=np.float64)
        rms, flatness = history[:, 0], history[:, 1]
        n = len(rms)
        active = np.count_nonzero(20.0 * np.log10(rms + 1e-9) > SILENCE_DB) / n
        if active < MIN_ACTIVE:
            return False
        mean = rms.mean()
        depth = rms.std() / (mean + 1e-9)

        low = 0
        for start in range(0, n, HOPS_PER_SEC):
            second = rms[start : start + HOPS_PER_SEC]
            low += np.count_nonzero(second < 0.5 * second.mean())
        low_energy = low / n

        flat = np.sort(flatness[rms > 0.3 * mean])
        median = flat[len(flat) // 2] if len(flat) else 1.0

        return bool(low_energy < MAX_LOW_ENERGY and depth < MAX_DEPTH and median < MAX_FLATNESS)

    def _hop(self, hop: np.ndarray, emit) -> None:
        energy = float(np.dot(hop, hop))
        lowpass, alpha, bass = self.lowpass, self.lowpass_alpha, 0.0
        for s in hop.tolist():
            lowpass += alpha * (s - lowpass)
            bass += lowpass * lowpass
        self.lowpass = lowpass
        rms = math.sqrt(energy / HOP)
        bass /= HOP
        db = to_db(rms)

        # A sudden jump well above what has been playing lately.
        self.since_loud += 1
        recent_peak = max((r for r, _ in list(self.history)[-LOUD_QUIET_BEFORE:]), default=0.0)
        if (
            self.since_loud >= LOUD_COOLDOWN
            and db > LOUD_DB
            and db > to_db(self.background) + LOUD_JUMP_DB
            and db > to_db(recent_peak) + LOUD_ABOVE_RECENT_DB
        ):
            self.since_loud = 0
            emit({"kind": "loud", "db": db})
        self.background += (rms - self.background) / (BACKGROUND_SECS * HOPS_PER_SEC)

        # Silent hops skip the FFT; their flatness is never looked at.
        flatness = self._flatness(hop) if db > SILENCE_DB else 1.0
        self.history.append((rms, flatness))

        # Hysteresis: the verdict has to hold for a few judgements in a row.
        self.since_judge += 1
        if len(self.history) == WINDOW and self.since_judge >= JUDGE_EVERY:
            self.since_judge = 0
            verdict = self._sounds_like_music()
            self.streak = self.streak + 1 if verdict != self.music else 0
            needed = OFF_AFTER if self.music else ON_AFTER
            if self.streak >= needed:
                self.streak = 0
                self.music = verdict
                emit({"kind": "music", "playing": verdict})

        # Beats: bass energy spiking above its average over the last second.
        self.since_beat += 1
        if self.bass_history:
            mean = sum(self.bass_history) / len(self.bass_history)
            if self.music and self.since_beat >= BEAT_MIN_GAP and bass > mean * BEAT_RATIO and bass > 1e-6:
                self.since_beat = 0
                emit({"kind": "beat", "strength": min(bass / max(mean, 1e-9), 10.0)})
        self.bass_history.append(bass)


def capture(enabled: threading.Event, sink) -> None:
    """Records what the default output plays and hands mono float32 blocks to `sink`.
    Returns when `enabled` is cleared or the default output device changes."""
    import soundcard as sc

    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    speaker = sc.default_speaker()
    # Opening a speaker as a "microphone" with loopback records what it plays.
    mic = sc.get_microphone(speaker.name, include_loopback=True)
    last_check = time.monotonic()
    with mic.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=HOP * 2) as recorder:
        while enabled.is_set():
            sink(recorder.record(numframes=HOP).mean(axis=1))
            # Follow the user when they switch the default output (e.g. to headphones).
            if time.monotonic() - last_check >= 3.0:
                last_check = time.monotonic()
                if sc.default_speaker().id != speaker.id:
                    return


def spawn(enabled: threading.Event, emit) -> threading.Thread:
    """Runs capture on a background thread for the lifetime of the app.
    Capture pauses while `enabled` is clear and restarts after device errors."""

    def run() -> None:
        while True:
            if not enabled.is_set():
                time.sleep(0.5)
                continue
            analyzer = Analyzer()
            error = None
            try:
                capture(enabled, lambda samples: analyzer.push(samples, emit))
            except Exception as e:
                error = e
            if analyzer.is_music():
                emit({"kind": "music", "playing": False})
            if error is not None:
                print(f"audio capture stopped: {error}", file=sys.stderr)
                time.sleep(3)

    thread = threading.Thread(target=run, name="clawd-audio", daemon=True)
    thread.start()
    return thread
