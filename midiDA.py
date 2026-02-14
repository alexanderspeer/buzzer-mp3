#!/usr/bin/env python3
"""
Inspect all data in a MIDI file at every hierarchical level.

Usage:
  python inspect-midi-hierarchy.py [midi_file.mid]

If no file is given, uses the first .mid/.midi file in the current directory.
Prints the full structure: MidiFile -> MidiTrack -> Message/MetaMessage with
all attributes at each level.
"""

import sys
from pathlib import Path

import mido


def to_serializable(obj):
    """Convert object to a JSON-friendly representation."""
    if obj is None:
        return None
    if isinstance(obj, (int, float, bool, str)):
        return obj
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}: {obj[:64]!r}{'...' if len(obj) > 64 else ''}>"
    if isinstance(obj, (list, tuple)):
        return [to_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_serializable(v) for k, v in obj.items()}
    if hasattr(obj, '__dict__'):
        return to_serializable(vars(obj))
    return repr(obj)


def message_to_dict(msg):
    """Extract all readable attributes from a Message or MetaMessage."""
    d = {}
    # Standard attributes
    for attr in dir(msg):
        if attr.startswith('_'):
            continue
        try:
            val = getattr(msg, attr)
            if callable(val):
                continue
            d[attr] = to_serializable(val)
        except Exception:
            pass
    return d


def print_section(title, indent=0):
    prefix = "  " * indent
    print(f"\n{prefix}{'=' * 60}")
    print(f"{prefix}{title}")
    print(f"{prefix}{'=' * 60}")


def print_dict(d, indent=0, max_value_len=80):
    prefix = "  " * indent
    for k, v in sorted(d.items()):
        vstr = repr(v)
        if len(vstr) > max_value_len:
            vstr = vstr[:max_value_len] + "..."
        print(f"{prefix}  {k}: {vstr}")


def inspect_midi_file(path: Path):
    mid = mido.MidiFile(path)

    print_section("LEVEL 0: MidiFile", 0)

    # MidiFile attributes
    file_attrs = {
        "path": str(path),
        "type": mid.type,  # 0=single track, 1=multi-track sync, 2=multi-track async
        "ticks_per_beat": mid.ticks_per_beat,
        "length": mid.length,  # total seconds (computed from tempo)
        "n_tracks": len(mid.tracks),
    }
    print_dict(file_attrs, 1)

    for track_idx, track in enumerate(mid.tracks):
        print_section(f"LEVEL 1: MidiTrack[{track_idx}]", 1)

        track_attrs = {
            "track_index": track_idx,
            "name": track.name,
            "n_messages": len(track),
        }
        print_dict(track_attrs, 2)

        # Sample first few and last few messages in full; summarize the middle
        max_full = 5
        max_total = 50  # cap total messages printed in full

        for msg_idx, msg in enumerate(track):
            if msg_idx >= max_total:
                remaining = len(track) - max_total
                print(f"\n  ... ({remaining} more messages) ...\n")
                break

            msg_type = getattr(msg, "type", type(msg).__name__)
            is_meta = "MetaMessage" in type(msg).__name__ or hasattr(msg, "type_byte")

            print(f"\n  --- Message[{msg_idx}] ({msg_type}) ---")
            d = message_to_dict(msg)
            print_dict(d, 2)

            # Extra: human-friendly note names for note_on/note_off
            if msg_type in ("note_on", "note_off") and "note" in d:
                note = d["note"]
                if isinstance(note, int):
                    note_names = "C C# D D# E F F# G G# A A# B".split()
                    octave = note // 12 - 2
                    name = note_names[note % 12]
                    print(f"      (note name: {name}{octave})")


def main():
    root = Path(__file__).resolve().parent

    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
    else:
        midi_files = sorted(list(root.glob("*.mid")) + list(root.glob("*.midi")))
        if not midi_files:
            print("No MIDI files found. Usage: python inspect-midi-hierarchy.py <file.mid>")
            return
        path = midi_files[0]
        print(f"No file specified, using: {path.name}")

    if not path.exists():
        print(f"File not found: {path}")
        return

    inspect_midi_file(path)


if __name__ == "__main__":
    main()
