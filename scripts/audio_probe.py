"""Runs Clawd's audio analyzer over WAV files and prints what it hears.

    python scripts/audio_probe.py clip1.wav clip2.wav

Files must be 16-bit PCM, mono, 22050 Hz, e.g. `sox in.mp3 -r 22050 -c 1 -b 16 out.wav`.
"""
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawd.audio import SAMPLE_RATE, Analyzer  # noqa: E402


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as f:
        shape = (f.getnchannels(), f.getframerate(), f.getsampwidth() * 8)
        if shape != (1, SAMPLE_RATE, 16):
            raise ValueError(f"need mono 16-bit {SAMPLE_RATE} Hz, got {shape}")
        return np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def probe(path: str) -> str:
    samples = read_wav(path)
    analyzer = Analyzer()
    log: list[str] = []
    stats = {"beats": 0, "on_since": None, "on_total": 0.0}
    step = SAMPLE_RATE // 10

    for i in range(0, len(samples), step):
        t = i / step / 10

        def emit(e, t=t):
            if e["kind"] == "music" and e["playing"]:
                stats["on_since"] = t
                log.append(f"music on @{t:.1f}s")
            elif e["kind"] == "music":
                if stats["on_since"] is not None:
                    stats["on_total"] += t - stats["on_since"]
                stats["on_since"] = None
                log.append(f"off @{t:.1f}s")
            elif e["kind"] == "loud":
                log.append(f"loud @{t:.1f}s")
            elif e["kind"] == "beat":
                stats["beats"] += 1

        analyzer.push(samples[i : i + step], emit)

    duration = len(samples) / SAMPLE_RATE
    if stats["on_since"] is not None:
        stats["on_total"] += duration - stats["on_since"]
    share = 100 * stats["on_total"] / duration if duration else 0
    return f"{Path(path).stem:28} {duration:5.1f}s  music {share:3.0f}%  beats {stats['beats']:3}  {', '.join(log)}"


def main() -> None:
    for path in sys.argv[1:]:
        try:
            print(probe(path))
        except (OSError, ValueError, wave.Error) as e:
            print(f"{path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
