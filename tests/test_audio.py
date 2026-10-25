import numpy as np

from clawd.audio import SAMPLE_RATE, Analyzer

SR = SAMPLE_RATE


def run(analyzer, samples):
    events = []
    analyzer.push(samples, events.append)
    return events


def silence(secs):
    return np.zeros(int(secs * SR), dtype=np.float32)


def tone(secs, freq, amp):
    t = np.arange(int(secs * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class Lcg:
    """Deterministic pseudo-random numbers in [0, 1)."""

    def __init__(self, seed):
        self.seed = seed

    def __call__(self):
        self.seed = (self.seed * 1_664_525 + 1_013_904_223) & 0xFFFFFFFF
        return (self.seed >> 8) / (1 << 24)


def song(secs, bpm):
    """A sustained C major chord with a decaying 60 Hz kick on every beat."""
    beat = 60.0 / bpm
    t = np.arange(int(secs * SR)) / SR
    chord = sum(0.12 * np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0))
    kick = 0.4 * np.exp(-(t % beat) * 12.0) * np.sin(2 * np.pi * 60.0 * t)
    return (chord + kick).astype(np.float32)


def speech(secs):
    """Voiced "syllables" (140 Hz harmonics) of ~180 ms, short gaps, and pauses between words."""
    rand = Lcg(7)
    out = []
    total = 0
    target = int(secs * SR)
    while total < target:
        for _ in range(3 + int(rand() * 4)):
            n = int((0.12 + rand() * 0.12) * SR)
            pitch = 120.0 + rand() * 50.0
            i = np.arange(n)
            env = np.sin(np.pi * i / n) ** 2
            t = (total + i) / SR
            voice = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 10))
            out.append((0.15 * env * voice).astype(np.float32))
            gap = silence(0.04 + rand() * 0.08)
            out.append(gap)
            total += n + len(gap)
        pause = silence(0.25 + rand() * 0.4)
        out.append(pause)
        total += len(pause)
    return np.concatenate(out)


def noise(secs, amp):
    rand = Lcg(1)
    return np.array([amp * (rand() * 2 - 1) for _ in range(int(secs * SR))], dtype=np.float32)


def music_events(events):
    return [e["playing"] for e in events if e["kind"] == "music"]


def kinds(events, kind):
    return [e for e in events if e["kind"] == kind]


def test_silence_is_quiet():
    assert run(Analyzer(), silence(10.0)) == []


def test_sudden_bang_is_loud_but_not_music():
    a = Analyzer()
    run(a, silence(3.0))
    events = run(a, tone(0.3, 1000.0, 0.7))
    assert [e["kind"] for e in events] == ["loud"], events
    assert run(a, silence(6.0)) == []


def test_quiet_notification_is_not_loud():
    a = Analyzer()
    run(a, silence(3.0))
    assert run(a, tone(0.3, 1000.0, 0.05)) == []


def test_song_turns_music_on_with_beats_and_off_after():
    a = Analyzer()
    events = run(a, song(9.0, 120.0))
    assert music_events(events) == [True], f"music within 9 s: {events}"
    events = run(a, song(5.0, 120.0))
    beats = len(kinds(events, "beat"))
    assert 8 <= beats <= 11, f"beats: {beats}"
    assert music_events(run(a, silence(5.0))) == [False]


def test_speech_is_not_music():
    events = run(Analyzer(), speech(30.0))
    assert music_events(events) == [], events


def test_speech_does_not_keep_startling():
    a = Analyzer()
    run(a, silence(3.0))
    events = run(a, speech(30.0) * 4.0)
    assert len(kinds(events, "loud")) <= 1, events


def test_steady_noise_is_not_music():
    events = run(Analyzer(), noise(20.0, 0.2))
    assert music_events(events) == [], events


def test_talking_after_a_song_ends_the_music():
    a = Analyzer()
    run(a, song(12.0, 100.0))
    assert a.is_music()
    assert music_events(run(a, speech(10.0))) == [False]


def test_chunk_size_does_not_matter():
    whole, pieces = Analyzer(), Analyzer()
    data = song(9.0, 120.0)
    a = run(whole, data)
    b = []
    for start in range(0, len(data), 1000):
        pieces.push(data[start : start + 1000], b.append)
    assert music_events(a) == music_events(b) == [True]
