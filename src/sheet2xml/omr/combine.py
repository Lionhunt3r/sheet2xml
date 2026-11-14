"""Make one score out of what several recognisers read from the same pages.

No recogniser is right everywhere, but they are wrong in different places, and each reads
signs the others do not know. One reading is the base (the recogniser that reads notes
best). Measure by measure it is compared with the others:

- a second recogniser read the same notes: the measure stands ("agreed");
- the others agree with each other against the base: their notes are taken ("outvoted");
- everyone differs: the base stands, and the measure is "disputed". Disputed measures are
  where the remaining errors sit; they are handed to the reviewer and shown to the user.

Then the base is given what it lacks: kinds of signs it has none of in a part (hairpins,
words, chord symbols, lyrics) are borrowed from the first other recogniser that has them.

A measure is compared by its notes only: when each note starts, how long it lasts and
which pitches sound. Voice numbers, stems, beams and rests are left out, because the
recognisers write them differently without meaning different music.
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction

from .cleanup import put_in_order

# who reads notes best comes first (measured with tools/scanbench.py)
ORDER = ("homr", "copisteria", "audiveris")
MAX_DIVISIONS = 20160  # beyond this a common grid for all recognisers gets unwieldy

Note = tuple[Fraction, Fraction, tuple[str, ...]]  # onset and length in quarter notes, pitches


@dataclass
class Reading:
    """One measure as one recogniser read it."""

    element: ET.Element
    notes: tuple[Note, ...]
    time: tuple[int, int]
    key: int
    divisions: int
    multirest: int = 1  # a multi-measure rest stands for this many measures
    totals: tuple[Fraction, ...] = ()  # how long each voice lasts, in quarter notes

    @property
    def empty(self) -> bool:
        return not self.notes

    @property
    def fills(self) -> bool:
        """Every voice lasts exactly as long as the time signature says."""
        return bool(self.totals) and all(t == Fraction(self.time[0] * 4, self.time[1]) for t in self.totals)


def _pitch(note: ET.Element) -> str:
    p = note.find("pitch")
    if p is None:
        u = note.find("unpitched")
        return "X" if u is None else f"X{u.findtext('display-step')}{u.findtext('display-octave')}"
    alter = int(float(p.findtext("alter") or 0))
    acc = "#" * alter if alter > 0 else "b" * -alter
    return f"{p.findtext('step')}{acc}{p.findtext('octave')}"


def read_part(part: ET.Element) -> list[Reading]:
    out: list[Reading] = []
    divisions, time, key = 1, (4, 4), 0
    for m in part.findall("measure"):
        multirest = 1
        for att in m.findall("attributes"):
            if att.findtext("divisions"):
                divisions = int(float(att.findtext("divisions")))
            if att.findtext("time/beats") and att.findtext("time/beat-type"):
                try:
                    time = (int(att.findtext("time/beats")), int(att.findtext("time/beat-type")))
                except ValueError:
                    pass
            if att.findtext("key/fifths"):
                key = int(att.findtext("key/fifths"))
            mr = att.findtext("measure-style/multiple-rest")
            if mr and mr.strip().isdigit():
                multirest = max(1, int(mr))
        pos = Fraction(0)
        last_onset = Fraction(0)
        chords: dict[tuple[Fraction, Fraction, str], list[str]] = {}
        totals: dict[str, Fraction] = {}
        for el in m:
            if el.tag == "note":
                if el.find("grace") is not None:
                    continue
                dur = Fraction(int(float(el.findtext("duration") or 0)), divisions)
                onset = last_onset if el.find("chord") is not None else pos
                voice = el.findtext("voice") or "1"
                if el.find("rest") is None:
                    chords.setdefault((onset, dur, voice), []).append(_pitch(el))
                if el.find("chord") is None:
                    last_onset = pos
                    pos += dur
                    totals[voice] = totals.get(voice, Fraction(0)) + dur
            elif el.tag == "backup":
                pos -= Fraction(int(float(el.findtext("duration") or 0)), divisions)
            elif el.tag == "forward":
                pos += Fraction(int(float(el.findtext("duration") or 0)), divisions)
        # voices are not compared: notes that start together and last equally long are one chord
        merged: dict[tuple[Fraction, Fraction], set[str]] = {}
        for (onset, dur, _voice), pitches in chords.items():
            merged.setdefault((onset, dur), set()).update(pitches)
        notes = tuple(sorted((onset, dur, tuple(sorted(p))) for (onset, dur), p in merged.items()))
        out.append(Reading(m, notes, time, key, divisions, multirest, tuple(totals.values())))
    return out


def similarity(a: Reading, b: Reading) -> float:
    """1.0 for the same notes, 0.0 for nothing in common."""
    if a.notes == b.notes:
        return 1.0
    sa, sb = set(a.notes), set(b.notes)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(len(sa), len(sb))


def align(a: list[Reading], b: list[Reading], gap: float = -0.4) -> list[tuple[int | None, int | None]]:
    """Pair the measures of two readings of one part (Needleman-Wunsch on the notes).

    A recogniser can miss a bar line or see one too many, so the counts need not match."""
    n, m = len(a), len(b)
    if n == m:
        return [(i, i) for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], move[i][0] = i * gap, 1
    for j in range(1, m + 1):
        score[0][j], move[0][j] = j * gap, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = score[i - 1][j - 1] + 2 * similarity(a[i - 1], b[j - 1]) - 0.5
            how = 0
            if score[i - 1][j] + gap > best:
                best, how = score[i - 1][j] + gap, 1
            if score[i][j - 1] + gap > best:
                best, how = score[i][j - 1] + gap, 2
            score[i][j], move[i][j] = best, how
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        how = move[i][j]
        if how == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif how == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


@dataclass
class Verdict:
    """What the recognisers say about one measure of the combined score."""

    part: int
    index: int  # measure index in the part
    number: str  # the measure's printed number
    state: str  # "agreed", "outvoted", "disputed" or "single" (nobody to compare with)
    chosen: str  # recogniser whose notes stand in the result
    agree: list[str] = field(default_factory=list)  # recognisers that read these notes
    others: dict[str, int | None] = field(default_factory=dict)  # recogniser -> its measure index


@dataclass
class Combined:
    xml: str
    base: str
    engines: list[str]  # recognisers that took part in the comparison
    verdicts: list[Verdict] = field(default_factory=list)
    borrowed: dict[str, str] = field(default_factory=dict)  # kind of sign -> recogniser it came from
    notes: list[str] = field(default_factory=list)  # what could not be used, for the user

    @property
    def disputed(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.state == "disputed"]

    @property
    def outvoted(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.state == "outvoted"]


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-8"))


def _all_divisions(root: ET.Element) -> set[int]:
    return {int(float(d.text)) for d in root.iter("divisions") if d.text and float(d.text) > 0}


def unify_divisions(roots: list[ET.Element]) -> int | None:
    """Rewrite all documents to one common `divisions`, so that notes can move between them.
    Returns it, or None (nothing changed) if the common grid would be too fine."""
    common = 1
    for root in roots:
        for d in _all_divisions(root):
            common = math.lcm(common, d)
    if common > MAX_DIVISIONS:
        return None
    for root in roots:
        for part in root.findall("part"):
            current = 1
            first = True
            for m in part.findall("measure"):
                for el in m.iter():
                    if el.tag == "divisions" and el.text:
                        current = int(float(el.text)) or 1
                        el.text = str(common)
                    elif el.tag == "duration" and el.text:
                        el.text = str(int(float(el.text)) * common // current)
                if first and m.find("attributes/divisions") is None:
                    att = ET.Element("attributes")
                    ET.SubElement(att, "divisions").text = str(common)
                    m.insert(0, att)
                first = False
    return common


def _same(a: Reading, b: Reading) -> bool:
    if a.notes != b.notes:
        return False
    # a long rest: both must count the same measures (a recogniser that cannot count them says 1)
    return not (a.multirest > 1 and b.multirest > 1 and a.multirest != b.multirest)


def _timeline(measure: ET.Element, divisions: int) -> list[tuple[ET.Element, Fraction]]:
    """Every child of a measure with the time at which it stands (quarter notes from the bar line)."""
    out = []
    pos = Fraction(0)
    last = Fraction(0)
    for el in measure:
        if el.tag == "note":
            if el.find("chord") is not None:
                out.append((el, last))
                continue
            out.append((el, pos))
            last = pos
            if el.find("grace") is None:
                pos += Fraction(int(float(el.findtext("duration") or 0)), divisions)
        elif el.tag == "backup":
            out.append((el, pos))
            pos -= Fraction(int(float(el.findtext("duration") or 0)), divisions)
        elif el.tag == "forward":
            out.append((el, pos))
            pos += Fraction(int(float(el.findtext("duration") or 0)), divisions)
        else:
            out.append((el, pos))
    return out


def _insert_at(measure: ET.Element, el: ET.Element, onset: Fraction, divisions: int) -> None:
    """Put a direction or chord symbol where the first voice reaches `onset`."""
    children = list(measure)
    index = len(children)
    for i, (child, at) in enumerate(_timeline(measure, divisions)):
        if child.tag == "backup" or (child.tag == "barline" and child.get("location") != "left" and i > 0):
            index = i
            break
        if child.tag == "note" and child.find("chord") is None and at >= onset:
            index = i
            break
    measure.insert(index, el)


def transplant(target: Reading, source: Reading) -> None:
    """Put the notes of `source` into the measure of `target`. The target keeps its attributes,
    bar lines and directions (dynamics, hairpins, words) where they stand in time.
    Both must be written in the same divisions (unify_divisions)."""
    m = target.element
    head: list[ET.Element] = []
    tail: list[ET.Element] = []
    kept: list[tuple[Fraction, ET.Element]] = []
    seen_note = False
    for el, at in _timeline(m, target.divisions):
        if el.tag in ("note", "backup", "forward"):
            seen_note = seen_note or el.tag == "note"
        elif el.tag in ("attributes", "print") and not seen_note:
            head.append(el)
        elif el.tag == "barline":
            (head if el.get("location") == "left" else tail).append(el)
        else:
            kept.append((at, el))
    for el in list(m):
        m.remove(el)
    m.extend(head)
    pending = sorted(kept, key=lambda k: k[0])
    in_first_voice = True
    for el, at in _timeline(source.element, source.divisions):
        if el.tag not in ("note", "backup", "forward"):
            continue
        if el.tag == "backup":
            in_first_voice = False
            m.extend(k[1] for k in pending)
            pending = []
        if in_first_voice and el.tag == "note" and el.find("chord") is None:
            while pending and pending[0][0] <= at:
                m.append(pending.pop(0)[1])
        m.append(copy.deepcopy(el))
    m.extend(k[1] for k in pending)
    m.extend(tail)


# kinds of signs one recogniser can lend another, and where to find them
DIRECTION_KINDS = ("wedge", "words", "metronome", "rehearsal", "segno", "coda", "dynamics", "pedal")
KIND_NAMES = {"wedge": "Gabeln", "words": "Textangaben", "metronome": "Metronomangaben", "rehearsal": "Studierzeichen",
              "segno": "Segno", "coda": "Coda", "dynamics": "Lautstärken", "pedal": "Pedal", "harmony": "Akkordsymbole",
              "lyric": "Liedtext"}


def _count(part: ET.Element, kind: str) -> int:
    if kind == "harmony":
        return len(part.findall("measure/harmony"))
    if kind == "lyric":
        return len(part.findall("measure/note/lyric"))
    return len(part.findall(f"measure/direction/direction-type/{kind}"))


def _direction_of(kind: str, direction: ET.Element) -> ET.Element | None:
    """A copy of the direction holding only its signs of this kind."""
    types = [dt for dt in direction.findall("direction-type") if dt.find(kind) is not None]
    if not types:
        return None
    out = ET.Element("direction", dict(direction.attrib))
    for dt in types:
        new = ET.SubElement(out, "direction-type")
        for child in dt:
            if child.tag == kind:
                new.append(copy.deepcopy(child))
    staff = direction.find("staff")
    if staff is not None:
        out.append(copy.deepcopy(staff))
    return out


def _lend(kind: str, source: ET.Element, target: ET.Element, divisions: int) -> None:
    """Copy the signs of one kind from a measure into the measure that is the same music."""
    if kind == "lyric":
        notes = [(el, at) for el, at in _timeline(target, divisions)
                 if el.tag == "note" and el.find("chord") is None and el.find("rest") is None
                 and el.find("grace") is None]
        free = list(notes)
        for el, at in _timeline(source, divisions):
            lyrics = el.findall("lyric") if el.tag == "note" else []
            if not lyrics or not free:
                continue
            best = min(free, key=lambda n: abs(n[1] - at))
            if abs(best[1] - at) > Fraction(1, 2):
                continue
            free.remove(best)
            for ly in lyrics:
                best[0].append(copy.deepcopy(ly))
        return
    for el, at in _timeline(source, divisions):
        if kind == "harmony" and el.tag == "harmony":
            _insert_at(target, copy.deepcopy(el), at, divisions)
        elif kind != "harmony" and el.tag == "direction":
            new = _direction_of(kind, el)
            if new is not None:
                _insert_at(target, new, at, divisions)


def _balance_wedges(part: ET.Element) -> None:
    """A hairpin needs its start and its end: drop halves whose other half fell into a measure
    that could not be matched."""
    open_start: tuple[ET.Element, ET.Element] | None = None
    for m in part.findall("measure"):
        for d in list(m.findall("direction")):
            for dt in d.findall("direction-type"):
                w = dt.find("wedge")
                if w is None:
                    continue
                if w.get("type") == "stop":
                    if open_start is None:
                        m.remove(d)
                    open_start = None
                else:
                    if open_start is not None:
                        open_start[0].remove(open_start[1])
                    open_start = (m, d)
    if open_start is not None:
        open_start[0].remove(open_start[1])


def _ottavas(part: ET.Element, divisions: int) -> list[tuple]:
    """The octave lines of a part: (measure index, time) of start and of end, the octaves the notes
    under it sound higher (8va: +1) or lower (8vb: -1) than written, and the two directions."""
    out = []
    start = None
    for mi, m in enumerate(part.findall("measure")):
        for el, at in _timeline(m, divisions):
            for shift in el.findall("direction-type/octave-shift") if el.tag == "direction" else []:
                kind = shift.get("type")
                if kind in ("down", "up"):
                    octaves = max(1, (int(shift.get("size") or 8) - 1) // 7)
                    start = ((mi, at), octaves if kind == "down" else -octaves, el)
                elif kind == "stop" and start is not None:
                    out.append((start[0], (mi, at), start[1], start[2], el))
                    start = None
    return out


def _apply_ottavas(mine: list[Reading], lender: ET.Element, pairs: dict[int, int], divisions: int) -> int:
    """A recogniser that does not know the 8va line writes the notes under it an octave off.
    Where another recogniser saw such a line, and the base's notes fit that recogniser's better
    once moved by the octave, move them and write the line. Returns the lines taken over."""
    back = {j: i for i, j in pairs.items()}
    theirs = lender.findall("measure")
    taken = 0
    for (j0, t0), (j1, t1), octaves, start_el, stop_el in _ottavas(lender, divisions):
        under: list[tuple[ET.Element, str]] = []  # the base's notes under the line, and the lender's pitch there
        for j in range(j0, j1 + 1):
            if j not in back:
                continue
            want = {}
            for el, at in _timeline(theirs[j], divisions):
                if el.tag == "note" and el.find("pitch") is not None and el.find("chord") is None:
                    want.setdefault(at, _pitch(el))
            for el, at in _timeline(mine[back[j]].element, divisions):
                if el.tag != "note" or el.find("pitch") is None:
                    continue
                if (j, at) < (j0, t0) or (j, at) >= (j1, t1):
                    continue
                under.append((el, want.get(at, "")))
        if not under or j0 not in back or j1 not in back:
            continue

        def moved(el: ET.Element) -> str:
            p = el.find("pitch")
            return f"{_pitch(el)[:-len(p.findtext('octave'))]}{int(p.findtext('octave')) + octaves}"

        chord_free = [(el, w) for el, w in under if el.find("chord") is None and w]
        as_written = sum(_pitch(el) == w for el, w in chord_free)
        as_moved = sum(moved(el) == w for el, w in chord_free)
        if as_moved <= as_written or 2 * as_moved < len(chord_free):
            continue
        for el, _ in under:
            octave = el.find("pitch/octave")
            octave.text = str(int(octave.text) + octaves)
        for el, (j, at) in ((start_el, (j0, t0)), (stop_el, (j1, t1))):
            new = ET.Element("direction", dict(el.attrib))
            for shift in el.findall("direction-type/octave-shift"):
                ET.SubElement(new, "direction-type").append(copy.deepcopy(shift))
            _insert_at(mine[back[j]].element, new, at, divisions)
        taken += 1
    return taken


def _tie_kinds(note: ET.Element) -> set[str]:
    return {t.get("type") for t in note.findall("tie")} | {t.get("type") for t in note.findall("notations/tied")}


def _set_ties(note: ET.Element, kinds: set[str]) -> None:
    """Make a note carry exactly these ties ("start", "stop"), as sound and as notation."""
    for t in note.findall("tie"):
        note.remove(t)
    for notations in note.findall("notations"):
        for t in notations.findall("tied"):
            notations.remove(t)
        if len(notations) == 0:
            note.remove(notations)
    if not kinds:
        return
    notations = note.find("notations")
    if notations is None:
        notations = ET.SubElement(note, "notations")
    for kind in ("stop", "start"):
        if kind in kinds:
            ET.SubElement(note, "tie", type=kind)
            notations.insert(0, ET.Element("tied", type=kind))


def _sounding(measure: ET.Element, divisions: int) -> list[tuple[Fraction, Fraction, str, ET.Element]]:
    """(onset, end, pitch, note) of every sounding note of a measure."""
    out = []
    for el, at in _timeline(measure, divisions):
        if el.tag == "note" and el.find("rest") is None and el.find("grace") is None:
            out.append((at, at + Fraction(int(float(el.findtext("duration") or 0)), divisions), _pitch(el), el))
    return out


def _lend_ties(target: ET.Element, source: ET.Element, divisions: int) -> None:
    """Two recognisers read the same notes in this measure: a tie that one of them saw is taken."""
    theirs = {(at, pitch): _tie_kinds(el) for at, _, pitch, el in _sounding(source, divisions)}
    for at, _, pitch, el in _sounding(target, divisions):
        have = _tie_kinds(el)
        want = have | theirs.get((at, pitch), set())
        if want != have:
            _set_ties(el, want)


def _balance_ties(part: ET.Element, divisions: int) -> None:
    """A tie joins a note to the next note of the same pitch that starts where it ends.
    Halves of ties without such a partner are dropped, missing second halves are added."""
    notes = []  # (start, end, pitch, note) on one time line through the part
    start = Fraction(0)
    for m in part.findall("measure"):
        here = _sounding(m, divisions)
        notes += [(start + a, start + b, pitch, el) for a, b, pitch, el in here]
        length = max((b for _, b, _, _ in here), default=Fraction(0))
        for el, at in _timeline(m, divisions):  # rests count for the length of the measure too
            if el.tag == "note" and el.find("chord") is None and el.find("grace") is None:
                length = max(length, at + Fraction(int(float(el.findtext("duration") or 0)), divisions))
        start += length
    by_start = {(a, pitch): el for a, _, pitch, el in notes}
    arrives: set[int] = set()
    kinds = {id(el): _tie_kinds(el) for _, _, _, el in notes}
    for _, end, pitch, el in notes:
        if "start" in kinds[id(el)]:
            partner = by_start.get((end, pitch))
            if partner is None or partner is el:
                kinds[id(el)] = kinds[id(el)] - {"start"}
            else:
                arrives.add(id(partner))
    for _, _, _, el in notes:
        want = (kinds[id(el)] - {"stop"}) | ({"stop"} if id(el) in arrives else set())
        if want != _tie_kinds(el):
            _set_ties(el, want)


def _rest_runs(readings: list[Reading]) -> list[tuple[int, int]]:
    """The long rests of a part: (index of the measure that carries the count, the count)."""
    return [(i, r.multirest) for i, r in enumerate(readings) if r.multirest > 1]


def _lengthen_rests(part: ET.Element, mine: list[Reading], other: list[Reading], pairs: dict[int, int]) -> int:
    """The number over a long rest is read badly, and mostly too low (a digit lost, a 6 taken
    for a 4). Where two recognisers saw the same long rest and counted differently, the larger
    count stands: the base gets the missing rest measures. Returns the rests lengthened."""
    sounding = [i for i, r in enumerate(mine) if not r.empty and i in pairs]
    bounds = [(-1, -1), *((i, pairs[i]) for i in sounding), (len(mine), len(other))]
    changes: list[tuple[int, int, int]] = []
    for (a0, b0), (a1, b1) in zip(bounds, bounds[1:]):
        if b1 <= b0:
            continue
        here = [(i, n) for i, n in _rest_runs(mine) if a0 < i < a1]
        there = [(j, n) for j, n in _rest_runs(other) if b0 < j < b1]
        if len(here) != len(there):
            continue
        changes += [(i, n, m) for (i, n), (_, m) in zip(here, there) if m > n]
    measures = part.findall("measure")
    for i, n, m in sorted(changes, reverse=True):
        last = min(i + n - 1, len(measures) - 1)
        if any(not mine[k].empty for k in range(i, last + 1)):
            continue
        filler = ET.Element("measure", {"number": "0"})
        for el in measures[last]:
            if el.tag in ("note", "backup", "forward"):
                filler.append(copy.deepcopy(el))
        if filler.find("note") is None:
            continue
        at = list(part).index(measures[last]) + 1
        for _ in range(m - n):
            part.insert(at, copy.deepcopy(filler))
        measures[i].find("attributes/measure-style/multiple-rest").text = str(m)
    if changes:
        first = part.find("measure")
        start = 0 if first is not None and first.get("implicit") == "yes" else 1
        for k, measure in enumerate(part.findall("measure")):
            measure.set("number", str(start + k))
    return len(changes)


def combine(readings: dict[str, str], order: tuple[str, ...] = ORDER) -> Combined:
    """One MusicXML out of the MusicXML of several recognisers (name -> text) for the same pages."""
    names = [n for n in order if n in readings] + [n for n in readings if n not in order]
    notes: list[str] = []
    roots: dict[str, ET.Element] = {}
    for name in names:
        try:
            root = _parse(readings[name])
        except ET.ParseError:
            notes.append(f"{name}: Ergebnis nicht lesbar")
            continue
        if root.find("part/measure") is None:
            notes.append(f"{name}: keine Noten erkannt")
            continue
        roots[name] = root
    if not roots:
        raise ValueError("kein Erkenner hat ein lesbares Ergebnis geliefert")
    base = next(iter(roots))
    base_root = roots[base]
    base_parts = base_root.findall("part")
    used = [base]
    for name in list(roots)[1:]:
        if len(roots[name].findall("part")) == len(base_parts):
            used.append(name)
        else:
            notes.append(f"{name}: andere Zahl von Stimmen als {base}, nicht verglichen")
    divisions = unify_divisions([roots[n] for n in used])
    result = Combined(xml="", base=base, engines=used, notes=notes)
    if divisions is None:
        notes.append("Die Erkenner zählen die Notenwerte zu verschieden, ihre Takte wurden nur verglichen")
    for pi, part in enumerate(base_parts):
        mine = read_part(part)
        theirs = {n: read_part(roots[n].findall("part")[pi]) for n in used[1:]}

        def matched(other: list[Reading]) -> dict[int, int]:
            return {i: j for i, j in align(mine, other) if i is not None and j is not None}

        for n, other in theirs.items():
            if _rest_runs(mine) and _lengthen_rests(part, mine, other, matched(other)):
                result.borrowed["Länge langer Pausen"] = n
                mine = read_part(part)
        maps = {n: matched(other) for n, other in theirs.items()}
        if divisions is not None and part.find("measure/direction/direction-type/octave-shift") is None:
            for n in theirs:
                if _apply_ottavas(mine, roots[n].findall("part")[pi], maps[n], divisions):
                    result.borrowed["Oktavierungen"] = n
                    mine = read_part(part)
                    break
        for i, r in enumerate(mine):
            seen = {n: theirs[n][maps[n][i]] for n in theirs if i in maps[n]}
            v = Verdict(part=pi, index=i, number=r.element.get("number") or str(i + 1), state="single", chosen=base,
                        others={n: maps[n].get(i) for n in theirs})
            if not theirs:
                result.verdicts.append(v)
                continue
            same = [n for n, o in seen.items() if _same(r, o)]
            if same:
                v.state, v.agree = "agreed", [base, *same]
                if divisions is not None:
                    for n in same:
                        _lend_ties(r.element, seen[n].element, divisions)
            else:
                against = None
                names_seen = list(seen)
                for a in range(len(names_seen)):
                    for b in range(a + 1, len(names_seen)):
                        if _same(seen[names_seen[a]], seen[names_seen[b]]):
                            against = against or (names_seen[a], names_seen[b])
                if against and divisions is not None:
                    v.state, v.chosen, v.agree = "outvoted", against[0], list(against)
                    transplant(r, seen[against[0]])
                else:
                    v.state = "disputed"
                    # nobody agrees, but a reading whose rhythm adds up beats one whose rhythm does not
                    inner = 0 < i < len(mine) - 1  # an upbeat and the measure that completes it may be short
                    better = next((n for n, o in seen.items() if o.fills and o.time == r.time), None)
                    if inner and not r.fills and better and divisions is not None:
                        v.chosen = better
                        transplant(r, seen[better])
            result.verdicts.append(v)
        if divisions is None:
            continue
        if theirs:
            _balance_ties(part, divisions)
        # what the base has none of in this part, the first other recogniser that has it lends
        for kind in (*DIRECTION_KINDS, "harmony", "lyric"):
            if _count(part, kind):
                continue
            lender = next((n for n in theirs if _count(roots[n].findall("part")[pi], kind)), None)
            if lender is None:
                continue
            for i, r in enumerate(mine):
                if i in maps[lender]:
                    _lend(kind, theirs[lender][maps[lender][i]].element, r.element, divisions)
            if kind == "wedge":
                _balance_wedges(part)
            if _count(part, kind):
                result.borrowed[KIND_NAMES[kind]] = lender
    put_in_order(base_root)
    header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    result.xml = header + ET.tostring(base_root, encoding="unicode")
    return result
