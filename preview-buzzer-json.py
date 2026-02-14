#!/usr/bin/env python3
"""
Preview a .buzzer.json file as audio on macOS.

Reads the JSON, generates a WAV with sine tones (gate envelope, loudness),
writes to a temp file and plays it with afplay. No Arduino needed.

Usage:
  python preview-buzzer-json.py David_Bowie_-_Space_Oddity.buzzer.json
  python preview-buzzer-json.py   # plays all .buzzer.json in current dir

Requires: numpy (pip install numpy)
"""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("This script requires numpy. Install with: pip install numpy")
    sys.exit(1)

SAMPLE_RATE = 44100
MAX_AMP = 0.25  # avoid clipping when mixing
LOUDNESS_SCALE = (0.35, 0.65, 1.0)  # for 3 levels; extend if loudness_levels > 3


def loudness_to_amplitude(level: int, num_levels: int = 3) -> float:
    if level <= 0 or num_levels <= 0:
        return 0.0
    idx = min(level - 1, num_levels - 1)
    if num_levels <= len(LOUDNESS_SCALE):
        return LOUDNESS_SCALE[idx] * MAX_AMP
    return (level / num_levels) * MAX_AMP


# Minimum end-ramp (samples) so we never cut at full amplitude (prevents clicks)
MIN_DECAY_SAMPLES = 22  # ~0.5 ms at 44.1 kHz


def render_note(
    buf: np.ndarray,
    start_ms: float,
    duration_ms: float,
    gate_ms: float,
    frequency_hz: float,
    amplitude: float,
    sample_rate: int,
) -> None:
    start_samp = int(start_ms * sample_rate / 1000.0)
    num_samp = int(duration_ms * sample_rate / 1000.0)
    gate_samp = int(gate_ms * sample_rate / 1000.0)
    if start_samp < 0 or num_samp <= 0 or start_samp + num_samp > len(buf):
        return
    t = np.arange(num_samp, dtype=np.float64) / sample_rate
    phase = 2.0 * math.pi * frequency_hz * t
    tone = amplitude * np.sin(phase)
    # Gate envelope: full until gate, then linear decay to end. Always ramp to zero at end to avoid clicks.
    envelope = np.ones(num_samp)
    decay_start = min(gate_samp, num_samp - MIN_DECAY_SAMPLES) if num_samp > MIN_DECAY_SAMPLES else 0
    if decay_start < num_samp and decay_start >= 0:
        decay_len = num_samp - decay_start
        envelope[decay_start:] = np.linspace(1.0, 0.0, decay_len)
    tone *= envelope
    buf[start_samp : start_samp + num_samp] += tone


def events_to_wav(events: list, loudness_levels: int = 3) -> tuple:
    if not events:
        return np.zeros(0, dtype=np.float64), SAMPLE_RATE
    last = events[-1]
    end_ms = last["start_ms"] + last["duration_ms"]
    total_samples = int(end_ms * SAMPLE_RATE / 1000.0) + 1
    buf = np.zeros(total_samples, dtype=np.float64)
    for e in events:
        if e.get("rest"):
            continue
        amp = loudness_to_amplitude(e.get("loudness_level", 3), loudness_levels)
        render_note(
            buf,
            start_ms=e["start_ms"],
            duration_ms=e["duration_ms"],
            gate_ms=e.get("gate_ms", e["duration_ms"]),
            frequency_hz=e["frequency_hz"],
            amplitude=amp,
            sample_rate=SAMPLE_RATE,
        )
    # Clip to prevent overflow when converting to int16
    peak = np.max(np.abs(buf))
    if peak > 1.0:
        buf = buf / peak
    return buf, SAMPLE_RATE


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    samples_int16 = (samples * 32767).astype(np.int16)
    import wave
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples_int16.tobytes())


def main():
    root = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = sorted(root.glob("*.buzzer.json"))
    if not paths:
        print("No .buzzer.json files given or found in current directory.")
        sys.exit(1)
    for json_path in paths:
        if not json_path.exists():
            print("Not found:", json_path)
            continue
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("events", [])
        loudness_levels = data.get("loudness_levels", 3)
        if not events:
            print("No events in", json_path.name)
            continue
        samples, sr = events_to_wav(events, loudness_levels)
        duration_s = len(samples) / sr
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            write_wav(wav_path, samples, sr)
            print("Playing", json_path.name, "({:.1f}s)...".format(duration_s))
            subprocess.run(["afplay", str(wav_path)], check=True)
        finally:
            wav_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
