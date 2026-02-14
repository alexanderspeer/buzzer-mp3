#!/usr/bin/env python3
"""
Extract MIDI data into Arduino-friendly JSON for piezo buzzer playback.

- All numbers are integers (no floats) for reliable Arduino parsing
- Tempo changes deduplicated (one per tick)
- Leading rests trimmed so song starts at first note
- Minimum gate duration (30ms) for audibility
- Use --compact for smaller files, --no-trim to keep intro rests

Choir + fill: use --choir-track 7 --fill-track 1 to use track 7 as primary and
fill only prolonged pauses (default 600 ms) with track 1; shorter rests between
choir notes are kept as silence.

Output: <song>.buzzer.json
"""


import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido
from mido import tick2second, tempo2bpm

REST_NOTE = -1
DEFAULT_TEMPO_US = 500000  # 120 BPM
MIN_NOTE = 48
MAX_NOTE = 96
GATE_RATIO = 0.9
LOUDNESS_LEVELS = 3  # 1=soft, 2=medium, 3=loud (or use 5 for finer)
MIN_GATE_MS = 30  # minimum note duration for buzzer audibility

# Choir + fill mode: track numbers 1-based (e.g. 7 = choir, 1 = guitar)
DEFAULT_CHOIR_TRACK = 7
DEFAULT_FILL_TRACK = 1
PROLONGED_PAUSE_MS = 600  # rest longer than this is filled with fill track; shorter rests kept as silence


def note_to_frequency(note: int) -> float:
    """Convert MIDI note number to frequency in Hz. A4 = 69 = 440 Hz."""
    return 440.0 * math.pow(2, (note - 69) / 12.0)


def velocity_to_loudness(velocity: int, levels: int = LOUDNESS_LEVELS) -> int:
    """Map velocity 0-127 to 1..levels."""
    if velocity <= 0:
        return 0
    v = min(127, velocity)
    # Linear map: 1-127 -> 1..levels
    level = 1 + int((v - 1) * levels / 127)
    return min(levels, max(1, level))


def build_tempo_map(mid: mido.MidiFile) -> List[Tuple[int, int]]:
    """
    Build list of (tick, tempo_us) from set_tempo events.
    Deduplicates: for same tick, last tempo wins (no duplicate ticks).
    """
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
    """Convert absolute tick to milliseconds using tempo map."""
    total_seconds = 0.0
    prev_tick = 0
    prev_tempo = tempo_map[0][1]

    for t, tempo_us in tempo_map:
        if t > tick:
            break
        # Add segment from prev_tick to min(t, tick)
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


def track_contains_drum_channel(track: mido.MidiTrack) -> bool:
    for msg in track:
        if msg.type in ("note_on", "note_off") and getattr(msg, "channel", None) == 9:
            return True
    return False


def analyze_track(track: mido.MidiTrack) -> Dict:
    active: set = set()
    now = 0
    last = 0
    time_active = 0
    time_poly = 0
    note_on_count = 0
    pitches: List[int] = []

    for msg in track:
        if msg.time:
            now += msg.time
        dt = now - last
        if dt > 0 and active:
            time_active += dt
            if len(active) >= 2:
                time_poly += dt
        last = now

        if msg.type in ("note_on", "note_off"):
            if getattr(msg, "channel", None) == 9:
                continue
            note = msg.note
            is_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)
            if is_off:
                active.discard(note)
            else:
                active.add(note)
                note_on_count += 1
                pitches.append(note)

    mono_ratio = max(0.0, 1.0 - (time_poly / time_active)) if time_active > 0 else 0.0
    avg_pitch = sum(pitches) / len(pitches) if pitches else 69.0
    min_pitch = min(pitches) if pitches else 0
    max_pitch = max(pitches) if pitches else 0

    return {
        "time_active": int(time_active),
        "time_poly": int(time_poly),
        "mono_ratio": mono_ratio,
        "note_on_count": int(note_on_count),
        "avg_pitch": avg_pitch,
        "min_pitch": min_pitch,
        "max_pitch": max_pitch,
        "is_drum_track": track_contains_drum_channel(track),
        "track_name": track_name(track),
    }


def score_track(info: Dict) -> float:
    if info["is_drum_track"] or info["time_active"] <= 0 or info["note_on_count"] <= 0:
        return -1e9
    mono = info["mono_ratio"]
    notes = info["note_on_count"]
    center = 69.0
    pitch_penalty = abs(info["avg_pitch"] - center) / 24.0
    pitch_score = max(0.0, 1.0 - pitch_penalty)
    density_score = min(1.0, notes / 200.0)
    return (3.0 * mono) + (1.5 * pitch_score) + (0.5 * density_score)


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
    """
    Extract events as integers for Arduino-friendly JSON.
    Uses highest-active-note rule. Enforces MIN_GATE_MS for audibility.
    """
    track = mid.tracks[track_index]
    active: Dict[int, Tuple[int, int]] = {}  # note -> (start_tick, velocity)

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


def merge_choir_with_fill(
    choir_events: List[Dict],
    fill_events: List[Dict],
    prolonged_ms: float,
) -> List[Dict]:
    """
    Use choir as primary; fill only prolonged rests with fill-track notes.
    Short rests between choir notes are kept as silence.
    """
    merged: List[Dict] = []
    fill_notes = [e for e in fill_events if not e.get("rest", False)]

    for e in choir_events:
        if e.get("rest"):
            dur = e["duration_ms"]
            rest_start = e["start_ms"]
            rest_end = rest_start + dur
            if dur < prolonged_ms:
                merged.append(e)
                continue
            # Prolonged rest: add fill notes that fall in [rest_start, rest_end]
            in_window = [
                g for g in fill_notes
                if g["start_ms"] < rest_end and (g["start_ms"] + g["duration_ms"]) > rest_start
            ]
            in_window.sort(key=lambda x: x["start_ms"])
            current_ms = rest_start
            for g in in_window:
                g_start = g["start_ms"]
                g_end = g_start + g["duration_ms"]
                if g_start > current_ms:
                    gap_ms = g_start - current_ms
                    merged.append({
                        "start_ms": current_ms,
                        "duration_ms": int(round(gap_ms)),
                        "rest": True,
                    })
                merged.append(dict(g))
                current_ms = max(current_ms, g_end)
            if current_ms < rest_end:
                merged.append({
                    "start_ms": current_ms,
                    "duration_ms": int(round(rest_end - current_ms)),
                    "rest": True,
                })
        else:
            merged.append(dict(e))

    merged.sort(key=lambda x: x["start_ms"])
    return merged


def _trim_and_rebase_events(events: List[Dict]) -> None:
    """Trim leading rests and rebase start_ms so first event starts at 0."""
    while events and events[0].get("rest"):
        events.pop(0)
    if not events:
        return
    first_start = events[0]["start_ms"]
    for e in events:
        e["start_ms"] -= first_start


def process_midi(
    midi_path: Path,
    gate_ratio: float = GATE_RATIO,
    loudness_levels: int = LOUDNESS_LEVELS,
    trim_leading_rests: bool = True,
    choir_track: Optional[int] = None,
    fill_track: Optional[int] = None,
    prolonged_pause_ms: Optional[float] = None,
) -> Optional[Dict]:
    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat
    tempo_map = build_tempo_map(mid)
    initial_tempo = tempo_map[0][1]
    tempo_bpm = int(round(tempo2bpm(initial_tempo)))
    n_tracks = len(mid.tracks)

    # Choir + fill mode: primary track with fill track in prolonged pauses only
    if choir_track is not None and fill_track is not None:
        ci = choir_track - 1  # 1-based -> 0-based
        fi = fill_track - 1
        if ci < 0 or ci >= n_tracks or fi < 0 or fi >= n_tracks:
            return None
        prolonged_ms = prolonged_pause_ms if prolonged_pause_ms is not None else PROLONGED_PAUSE_MS
        choir_events = extract_events_with_velocity(
            mid, ci, tempo_map, tpb, gate_ratio, loudness_levels, trim_leading_rests=False
        )
        fill_events = extract_events_with_velocity(
            mid, fi, tempo_map, tpb, gate_ratio, loudness_levels, trim_leading_rests=False
        )
        events = merge_choir_with_fill(choir_events, fill_events, prolonged_ms)
        if trim_leading_rests:
            _trim_and_rebase_events(events)
        choir_name = track_name(mid.tracks[ci])
        fill_name = track_name(mid.tracks[fi])
        tempo_changes = [{"tick": t, "tempo_us": tempo_us, "bpm": int(round(tempo2bpm(tempo_us)))} for t, tempo_us in tempo_map]
        return {
            "source_midi": midi_path.name,
            "ticks_per_beat": int(tpb),
            "tempo_bpm": tempo_bpm,
            "tempo_changes": tempo_changes,
            "selected_track_index": int(ci),
            "selected_track_name": choir_name.strip(),
            "fill_track_index": int(fi),
            "fill_track_name": fill_name.strip(),
            "prolonged_pause_ms": int(round(prolonged_ms)),
            "gate_ratio": gate_ratio,
            "loudness_levels": loudness_levels,
            "note_range": {"min_note": MIN_NOTE, "max_note": MAX_NOTE},
            "events": events,
        }

    analyses = []
    for i, tr in enumerate(mid.tracks):
        info = analyze_track(tr)
        info["track_index"] = i
        info["score"] = score_track(info)
        analyses.append(info)

    best = max(analyses, key=lambda d: d["score"])
    if best["score"] < -1e8:
        return None

    events = extract_events_with_velocity(
        mid, best["track_index"], tempo_map, tpb, gate_ratio, loudness_levels, trim_leading_rests
    )

    tempo_changes = [{"tick": t, "tempo_us": tempo_us, "bpm": int(round(tempo2bpm(tempo_us)))} for t, tempo_us in tempo_map]

    return {
        "source_midi": midi_path.name,
        "ticks_per_beat": int(tpb),
        "tempo_bpm": tempo_bpm,
        "tempo_changes": tempo_changes,
        "selected_track_index": int(best["track_index"]),
        "selected_track_name": best["track_name"].strip(),
        "gate_ratio": gate_ratio,
        "loudness_levels": loudness_levels,
        "note_range": {"min_note": MIN_NOTE, "max_note": MAX_NOTE},
        "events": events,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Convert MIDI to Arduino-friendly buzzer JSON")
    parser.add_argument("--compact", action="store_true", help="Minimal JSON (no whitespace) for smaller files")
    parser.add_argument("--no-trim", action="store_true", help="Keep leading rests (do not trim)")
    parser.add_argument("--choir-track", type=int, default=None, metavar="N",
                        help="Primary track (1-based). With --fill-track, use choir as main and fill long pauses with fill track.")
    parser.add_argument("--fill-track", type=int, default=None, metavar="N",
                        help="Fill track (1-based). Fills prolonged pauses in choir track only.")
    parser.add_argument("--prolonged-ms", type=float, default=None, metavar="MS",
                        help="Rest longer than this (ms) is filled with fill track; shorter rests stay silent (default: %s)" % PROLONGED_PAUSE_MS)
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

    json_kw = {} if args.compact else {"indent": 2}

    choir = args.choir_track
    fill = args.fill_track
    if (choir is not None) != (fill is not None):
        print("For choir+fill mode, specify both --choir-track and --fill-track.")
        return

    for midi_path in midi_files:
        try:
            out = process_midi(
                midi_path,
                trim_leading_rests=not args.no_trim,
                choir_track=choir,
                fill_track=fill,
                prolonged_pause_ms=args.prolonged_ms,
            )
            if out is None:
                print(f"Skip {midi_path.name}: no suitable melody track.")
                continue

            out_path = midi_path.with_suffix(".buzzer.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out, f, **json_kw)

            n_events = len(out["events"])
            n_notes = sum(1 for e in out["events"] if not e.get("rest", False))
            if "fill_track_name" in out:
                print(f"Wrote {out_path.name}: {n_notes} notes, {n_events} events, choir={out['selected_track_name']}, fill={out['fill_track_name']}")
            else:
                print(f"Wrote {out_path.name}: {n_notes} notes, {n_events} events, track={out['selected_track_name']}")

        except Exception as e:
            print(f"Failed {midi_path.name}: {e}")


if __name__ == "__main__":
    main()
