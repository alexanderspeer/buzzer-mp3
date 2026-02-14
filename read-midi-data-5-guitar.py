#!/usr/bin/env python3
"""
Extract a single MIDI track to .buzzer.json for preview with preview-buzzer-json.py.
Full length, no cutoffs. Track number is 1-based (1 = first track).

Usage:
  python read-midi-data-5-guitar.py --track 3 song.mid
  python read-midi-data-5-guitar.py song.mid   # uses default track 2

Output: <song>.track<N>.buzzer.json  (e.g. song.track3.buzzer.json)
"""

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido
from mido import tick2second, tempo2bpm

DEFAULT_TEMPO_US = 500000
MIN_NOTE = 48
MAX_NOTE = 96
GATE_RATIO = 0.9
LOUDNESS_LEVELS = 3
MIN_GATE_MS = 30

DEFAULT_TRACK_1BASED = 2  # track 2 = index 1 (first track is often tempo-only)


def note_to_frequency(note: int) -> float:
    return 440.0 * math.pow(2, (note - 69) / 12.0)


def velocity_to_loudness(velocity: int, levels: int = LOUDNESS_LEVELS) -> int:
    if velocity <= 0:
        return 0
    v = min(127, velocity)
    level = 1 + int((v - 1) * levels / 127)
    return min(levels, max(1, level))


def build_tempo_map(mid: mido.MidiFile) -> List[Tuple[int, int]]:
    raw: List[Tuple[int, int]] = [(0, DEFAULT_TEMPO_US)]
    now = 0
    track = mid.tracks[0]
    for msg in track:
        now += msg.time
        if msg.type == "set_tempo":
            raw.append((now, msg.tempo))
    raw.sort(key=lambda x: x[0])
    tempo_map: List[Tuple[int, int]] = []
    for t, tempo_us in raw:
        if tempo_map and tempo_map[-1][0] == t:
            tempo_map[-1] = (t, tempo_us)
        else:
            tempo_map.append((t, tempo_us))
    return tempo_map


def tick_to_ms(tick: int, tempo_map: List[Tuple[int, int]], tpb: int) -> float:
    total_seconds = 0.0
    prev_tick = 0
    prev_tempo = tempo_map[0][1]
    for t, tempo_us in tempo_map:
        if t > tick:
            break
        end_tick = min(t, tick)
        if end_tick > prev_tick:
            delta_ticks = end_tick - prev_tick
            total_seconds += tick2second(delta_ticks, tpb, prev_tempo)
        prev_tick = end_tick
        prev_tempo = tempo_us
    if tick > prev_tick:
        delta_ticks = tick - prev_tick
        total_seconds += tick2second(delta_ticks, tpb, prev_tempo)
    return total_seconds * 1000.0


def track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.type == "track_name":
            return str(msg.name or "").strip()
    return ""


def transpose_into_range(note: int, min_n: int = MIN_NOTE, max_n: int = MAX_NOTE) -> int:
    while note < min_n:
        note += 12
    while note > max_n:
        note -= 12
    return max(min_n, min(max_n, note))


def extract_events_with_velocity(
    mid: mido.MidiFile,
    track_index: int,
    tempo_map: List[Tuple[int, int]],
    tpb: int,
    gate_ratio: float = GATE_RATIO,
    loudness_levels: int = LOUDNESS_LEVELS,
    trim_leading_rests: bool = True,
) -> List[Dict]:
    track = mid.tracks[track_index]
    active: Dict[int, Tuple[int, int]] = {}

    def chosen() -> Optional[Tuple[int, int, int]]:
        if not active:
            return None
        best_note = max(active.keys())
        start_tick, vel = active[best_note]
        return (best_note, start_tick, vel)

    segments: List[Tuple[int, int, int, int]] = []
    now = 0
    prev_chosen: Optional[Tuple[int, int, int]] = None

    for msg in track:
        now += msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        if getattr(msg, "channel", None) == 9:
            continue
        note = msg.note
        is_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)
        velocity = msg.velocity if not is_off else 0
        if is_off:
            if note in active:
                del active[note]
        else:
            active[note] = (now, velocity)
        cur = chosen()
        if prev_chosen is not None and cur != prev_chosen:
            pnote, pstart, pvel = prev_chosen
            duration_ticks = now - pstart
            if duration_ticks > 0:
                segments.append((pstart, now, pnote, pvel))
        prev_chosen = cur

    if prev_chosen is not None:
        pnote, pstart, pvel = prev_chosen
        last_tick = sum(m.time for m in track)
        duration_ticks = last_tick - pstart
        if duration_ticks > 0:
            segments.append((pstart, pstart + duration_ticks, pnote, pvel))

    events: List[Dict] = []
    segments.sort(key=lambda s: s[0])
    last_end_tick = 0

    for start_tick, end_tick, note, vel in segments:
        if start_tick > last_end_tick:
            rest_start_ms = tick_to_ms(last_end_tick, tempo_map, tpb)
            rest_end_ms = tick_to_ms(start_tick, tempo_map, tpb)
            rest_ms = rest_end_ms - rest_start_ms
            if rest_ms >= 1:
                events.append({
                    "start_ms": int(round(rest_start_ms)),
                    "duration_ms": int(round(rest_ms)),
                    "rest": True,
                })
        start_ms = tick_to_ms(start_tick, tempo_map, tpb)
        end_ms = tick_to_ms(end_tick, tempo_map, tpb)
        duration_ms = end_ms - start_ms
        gate_ms = max(MIN_GATE_MS, duration_ms * gate_ratio)
        transposed = transpose_into_range(note)
        freq_hz = note_to_frequency(transposed)
        events.append({
            "start_ms": int(round(start_ms)),
            "duration_ms": int(round(duration_ms)),
            "gate_ms": int(round(gate_ms)),
            "note": transposed,
            "frequency_hz": int(round(freq_hz)),
            "loudness_level": velocity_to_loudness(vel, loudness_levels),
            "velocity": vel,
        })
        last_end_tick = end_tick

    if trim_leading_rests:
        while events and events[0].get("rest"):
            events.pop(0)
        if events:
            first_start = events[0]["start_ms"]
            for e in events:
                e["start_ms"] -= first_start

    return events


def track_to_buzzer_json(midi_path: Path, track_1based: int) -> Optional[Dict]:
    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat
    tempo_map = build_tempo_map(mid)
    initial_tempo = tempo_map[0][1]
    tempo_bpm = int(round(tempo2bpm(initial_tempo)))
    track_index = track_1based - 1
    if track_index < 0 or track_index >= len(mid.tracks):
        return None

    events = extract_events_with_velocity(
        mid, track_index, tempo_map, tpb,
        gate_ratio=GATE_RATIO,
        loudness_levels=LOUDNESS_LEVELS,
        trim_leading_rests=True,
    )
    if not events:
        return None

    tempo_changes = [
        {"tick": t, "tempo_us": tempo_us, "bpm": int(round(tempo2bpm(tempo_us)))}
        for t, tempo_us in tempo_map
    ]
    name = track_name(mid.tracks[track_index])

    return {
        "source_midi": midi_path.name,
        "ticks_per_beat": int(tpb),
        "tempo_bpm": tempo_bpm,
        "tempo_changes": tempo_changes,
        "selected_track_index": int(track_index),
        "selected_track_name": name,
        "gate_ratio": GATE_RATIO,
        "loudness_levels": LOUDNESS_LEVELS,
        "note_range": {"min_note": MIN_NOTE, "max_note": MAX_NOTE},
        "events": events,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract one MIDI track to .buzzer.json for preview (track number 1-based)")
    parser.add_argument("--track", "-t", type=int, default=DEFAULT_TRACK_1BASED, metavar="N",
                        help="Track number (1-based), default %s" % DEFAULT_TRACK_1BASED)
    parser.add_argument("files", nargs="*", help="MIDI files (default: all .mid/.midi in current dir)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    if args.files:
        midi_files = [Path(f) for f in args.files]
    else:
        midi_files = sorted(list(root.glob("*.mid")) + list(root.glob("*.midi")))
    if not midi_files:
        print("No MIDI files found.")
        return

    for midi_path in midi_files:
        try:
            out = track_to_buzzer_json(midi_path, args.track)
            if out is None:
                print("Skip %s: no events on track %s or invalid track index." % (midi_path.name, args.track))
                continue

            out_path = midi_path.parent / ("%s.track%s.buzzer.json" % (midi_path.stem, args.track))
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)

            n_events = len(out["events"])
            n_notes = sum(1 for e in out["events"] if not e.get("rest", False))
            print("Wrote %s: %d notes, %d events, track %s=%s" % (
                out_path.name, n_notes, n_events, args.track, out["selected_track_name"]))

        except Exception as e:
            print("Failed %s: %s" % (midi_path.name, e))


if __name__ == "__main__":
    main()
