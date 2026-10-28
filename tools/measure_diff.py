"""Print measures that differ between a result and a reference (debug aid).

Usage: python tools/measure_diff.py result.musicxml reference.mxl PARTNAME [offset]
"""
import sys
import warnings

import music21 as m21

warnings.filterwarnings("ignore")


def measures(part):
    out = []
    for ms in part.getElementsByClass("Measure"):
        toks = []
        for n in ms.flatten().notesAndRests:
            if n.duration.isGrace:
                continue
            name = "R" if n.isRest else ".".join(sorted(p.nameWithOctave for p in n.pitches))
            toks.append(f"{name}:{float(n.quarterLength):g}")
        out.append(toks)
    return out


def main():
    res = m21.converter.parse(sys.argv[1])
    ref = m21.converter.parse(sys.argv[2])
    name = sys.argv[3]
    off = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    rp = [p for p in res.parts if (p.partName or "").startswith(name)][0]
    fp = [p for p in ref.parts if (p.partName or "").startswith(name)][0]
    a, b = measures(rp), measures(fp)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i + off] if 0 <= i + off < len(b) else None
        if x != y:
            print(i + 1, "RES", x)
            print(i + 1, "REF", y)


if __name__ == "__main__":
    main()
