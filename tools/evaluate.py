"""Compare a recognised MusicXML file with a reference.

Usage: python tools/evaluate.py result.musicxml reference.mxl [--part NAME] [--ref-part NAME]

For every part the notes (and rests) are flattened to tokens and aligned with
difflib, so a shifted pickup or a missing measure does not ruin the score.
Prints the share of matching tokens for pitch+duration and for pitch only.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import warnings

import music21 as m21

warnings.filterwarnings("ignore")


def tokens(part, with_rests: bool = True, with_dur: bool = True):
    out = []
    for el in part.flatten().notesAndRests:
        if el.duration.isGrace:
            continue
        if el.isRest:
            if not with_rests:
                continue
            name = "R"
        elif el.isChord:
            name = ".".join(sorted(p.nameWithOctave for p in el.pitches))
        else:
            name = el.pitch.nameWithOctave
        if with_dur:
            out.append(f"{name}:{float(el.quarterLength):.3f}")
        else:
            out.append(name)
    return out


def merge_rests(toks):
    """Consecutive rests compare as one (a 3-bar rest equals three whole rests)."""
    out = []
    for t in toks:
        if t.startswith("R") and out and out[-1].startswith("R"):
            a = float(out[-1].split(":")[1]) if ":" in out[-1] else 0
            b = float(t.split(":")[1]) if ":" in t else 0
            out[-1] = f"R:{a + b:.3f}" if ":" in t else "R"
        else:
            out.append(t)
    return out


def ratio(a, b) -> float:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = sum(bl.size for bl in sm.get_matching_blocks())
    return match / max(1, max(len(a), len(b)))


def compare_parts(res_part, ref_part) -> dict:
    a = [t for t in merge_rests(tokens(res_part))]
    b = [t for t in merge_rests(tokens(ref_part))]
    an = tokens(res_part, with_rests=False, with_dur=False)
    bn = tokens(ref_part, with_rests=False, with_dur=False)
    return {
        "notes_result": len(an),
        "notes_reference": len(bn),
        "pitch_and_rhythm": round(ratio(a, b), 3),
        "pitch_only": round(ratio(an, bn), 3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("reference")
    ap.add_argument("--part", help="part name in the result (default: all, matched by order)")
    ap.add_argument("--ref-part", help="part name in the reference")
    args = ap.parse_args(argv)
    res = m21.converter.parse(args.result)
    ref = m21.converter.parse(args.reference)
    pairs = []
    if args.ref_part:
        rp = next(p for p in ref.parts if (p.partName or "").strip().lower().startswith(args.ref_part.lower()))
        sp = res.parts[0] if not args.part else next(
            p for p in res.parts if (p.partName or "").lower().startswith(args.part.lower())
        )
        pairs.append((sp, rp))
    else:
        pairs = list(zip(res.parts, ref.parts))
    for sp, rp in pairs:
        r = compare_parts(sp, rp)
        print(f"{sp.partName!r:30} vs {rp.partName!r:30} {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
