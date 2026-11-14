"""Compare a recognised MusicXML file with a reference.

Usage: python tools/evaluate.py result.musicxml reference.mxl [--part NAME] [--ref-part NAME]

For every part the notes (and rests) are flattened to tokens and aligned with
difflib, so a shifted pickup or a missing measure does not ruin the score.
Prints the share of matching tokens for pitch+duration and for pitch only, and
how many of the other signs (dynamics, hairpins, slurs, tremolos, ...) were found.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import warnings
import xml.etree.ElementTree as ET
import zipfile

import music21 as m21

warnings.filterwarnings("ignore")


def tokens(part, with_rests: bool = True, with_dur: bool = True):
    out = []
    for el in part.flatten().notesAndRests:
        if el.duration.isGrace or el.isRest and el.style.hideObjectOnPrint:
            continue  # grace notes and invisible filler rests
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


def matched(a, b) -> int:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(bl.size for bl in sm.get_matching_blocks())


def read_xml(path: str) -> ET.Element:
    """Root of a MusicXML file, plain or compressed (.mxl)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith((".xml", ".musicxml")) and not n.startswith("META-INF"))
            return ET.fromstring(z.read(name))
    return ET.parse(path).getroot()


def count_marks(root: ET.Element) -> dict[str, int]:
    """How many of each kind of sign a MusicXML document carries (everything that is not pitch or rhythm)."""
    notes = root.findall(".//note")
    return {
        "dynamics": sum(len(d) for d in root.iter("dynamics")),
        "hairpins": sum(1 for w in root.iter("wedge") if w.get("type") in ("crescendo", "diminuendo")),
        "slurs": sum(1 for s in root.iter("slur") if s.get("type") == "start"),
        "ties": sum(1 for n in notes if any(t.get("type") == "start" for t in n.findall("tie"))),
        "tremolos": sum(1 for _ in root.iter("tremolo")),
        "articulations": sum(len(a) for a in root.iter("articulations")),
        "fermatas": sum(1 for _ in root.iter("fermata")),
        "tuplet_notes": sum(1 for n in notes if n.find("time-modification") is not None),
        "grace_notes": sum(1 for n in notes if n.find("grace") is not None),
        "lyrics": sum(1 for n in notes for _ in n.findall("lyric")),
        "chord_symbols": sum(1 for _ in root.iter("harmony")),
        "words": sum(1 for w in root.iter("words") if (w.text or "").strip()),
    }


def score(result: str, reference: str) -> dict:
    """One set of numbers for a whole piece: the notes of all parts pooled, and every kind of sign counted.

    Parts are paired in order; a part the result lacks counts as all wrong."""
    res = m21.converter.parse(result)
    ref = m21.converter.parse(reference)
    full = pitch = total_full = total_pitch = 0
    res_parts = list(res.parts)
    for i, rp in enumerate(ref.parts):
        b = merge_rests(tokens(rp))
        bn = tokens(rp, with_rests=False, with_dur=False)
        a = merge_rests(tokens(res_parts[i])) if i < len(res_parts) else []
        an = tokens(res_parts[i], with_rests=False, with_dur=False) if i < len(res_parts) else []
        full += matched(a, b)
        pitch += matched(an, bn)
        total_full += max(len(a), len(b))
        total_pitch += max(len(an), len(bn))
    found, expected = count_marks(read_xml(result)), count_marks(read_xml(reference))
    return {
        "parts_result": len(res_parts),
        "parts_reference": len(ref.parts),
        "pitch_and_rhythm": round(full / max(1, total_full), 3),
        "pitch_only": round(pitch / max(1, total_pitch), 3),
        "marks": {k: [found[k], expected[k]] for k in expected},
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
    found, expected = count_marks(read_xml(args.result)), count_marks(read_xml(args.reference))
    print("signs found / in the reference:",
          ", ".join(f"{k} {found[k]}/{expected[k]}" for k in expected if found[k] or expected[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
