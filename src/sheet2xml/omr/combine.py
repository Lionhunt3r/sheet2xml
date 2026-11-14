"""Compare what several recognisers read from the same pages, measure by measure.

No recogniser is right everywhere, but they are wrong in different places. Where two of
three read the same notes we take that reading; where all differ the measure is disputed
and is handed to the reviewer (Claude with the image) or marked for the user.

A measure is compared by its notes only: when each note starts, how long it lasts and
which pitches sound. Voice numbers, stems, beams and rests are left out, because the
recognisers write them differently without meaning different music.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction

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

    @property
    def empty(self) -> bool:
        return not self.notes


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
        for el in m:
            if el.tag == "note":
                if el.find("grace") is not None:
                    continue
                dur = Fraction(int(float(el.findtext("duration") or 0)), divisions)
                onset = last_onset if el.find("chord") is not None else pos
                if el.find("rest") is None:
                    chords.setdefault((onset, dur, el.findtext("voice") or "1"), []).append(_pitch(el))
                if el.find("chord") is None:
                    last_onset = pos
                    pos += dur
            elif el.tag == "backup":
                pos -= Fraction(int(float(el.findtext("duration") or 0)), divisions)
            elif el.tag == "forward":
                pos += Fraction(int(float(el.findtext("duration") or 0)), divisions)
        # voices are not compared: notes that start together and last equally long are one chord
        merged: dict[tuple[Fraction, Fraction], set[str]] = {}
        for (onset, dur, _voice), pitches in chords.items():
            merged.setdefault((onset, dur), set()).update(pitches)
        notes = tuple(sorted((onset, dur, tuple(sorted(p))) for (onset, dur), p in merged.items()))
        out.append(Reading(m, notes, time, key, divisions, multirest))
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
    """What the recognisers say about one measure of the base reading."""

    part: int
    index: int  # measure index in the base part
    agree: list[str]  # recognisers that read the chosen notes
    chosen: str  # recogniser whose notes are taken
    disputed: bool  # no two recognisers agree


@dataclass
class Combined:
    xml: str
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def disputed(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.disputed]

    @property
    def replaced(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.disputed and v.agree and v.chosen != v.agree[0] or False]


def expand_multirests(readings: list[Reading]) -> list[Reading]:
    """A multi-measure rest written as one measure becomes as many empty measures as it stands for,
    so that it lines up with a recogniser that writes them out."""
    out = []
    for r in readings:
        out.append(r)
        for _ in range(r.multirest - 1):
            out.append(Reading(r.element, (), r.time, r.key, r.divisions, 0))
    return out


def _voice_content(measure: ET.Element) -> list[ET.Element]:
    return [el for el in measure if el.tag in ("note", "backup", "forward")]


def _scaled(el: ET.Element, num: int, den: int) -> ET.Element:
    el = copy.deepcopy(el)
    d = el.find("duration")
    if d is not None and d.text:
        d.text = str(int(float(d.text)) * num // den)
    return el


def transplant(target: Reading, source: Reading) -> None:
    """Put the notes of `source` into the measure of `target`, keeping the target's directions
    (dynamics, hairpins, words), attributes and bar lines where they stand in time."""
    m = target.element
    # where each kept element stood, in quarter notes from the start of the measure
    kept: list[tuple[Fraction, ET.Element]] = []
    head: list[ET.Element] = []
    tail: list[ET.Element] = []
    pos = Fraction(0)
    seen_note = False
    for el in list(m):
        if el.tag == "note":
            seen_note = True
            if el.find("chord") is None and el.find("grace") is None:
                pos += Fraction(int(float(el.findtext("duration") or 0)), target.divisions)
        elif el.tag == "backup":
            pos -= Fraction(int(float(el.findtext("duration") or 0)), target.divisions)
        elif el.tag == "forward":
            pos += Fraction(int(float(el.findtext("duration") or 0)), target.divisions)
        elif el.tag in ("attributes", "print") and not seen_note:
            head.append(el)
        elif el.tag == "barline" and el.get("location") != "left":
            tail.append(el)
        elif el.tag == "barline":
            head.append(el)
        else:
            kept.append((pos, el))
    for el in list(m):
        m.remove(el)
    for el in head:
        m.append(el)
    # the source's durations are in its own divisions: bring both to a common one
    div = target.divisions
    num, den = div, source.divisions
    if (div * 1) % source.divisions:
        # the target's divisions cannot express the source's durations: adopt a finer grid
        num, den = 1, 1
        att = ET.Element("attributes")
        ET.SubElement(att, "divisions").text = str(source.divisions)
        m.append(att)
        restore = ET.Element("attributes")
        ET.SubElement(restore, "divisions").text = str(div)
        tail.insert(0, restore)
        div = source.divisions
    pos = Fraction(0)
    pending = sorted(kept, key=lambda k: k[0])
    for el in _voice_content(source.element):
        el = _scaled(el, num, den)
        starts = pos
        if el.tag == "note":
            if el.find("chord") is None and el.find("grace") is None:
                while pending and pending[0][0] <= starts:
                    m.append(pending.pop(0)[1])
                pos += Fraction(int(float(el.findtext("duration") or 0)), div)
        elif el.tag == "backup":
            pos -= Fraction(int(float(el.findtext("duration") or 0)), div)
        else:
            pos += Fraction(int(float(el.findtext("duration") or 0)), div)
        m.append(el)
    for _, el in pending:
        m.append(el)
    for el in tail:
        m.append(el)
