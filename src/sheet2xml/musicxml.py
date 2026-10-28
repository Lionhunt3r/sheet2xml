"""Write the score model as MusicXML 4.0 (partwise), the format Sibelius,
MuseScore, Dorico and Finale import."""

from __future__ import annotations

import math
from fractions import Fraction
from xml.sax.saxutils import escape

from . import __version__
from .model import Event, Measure, Part, Score

TYPE_NAMES = {
    Fraction(8): "breve",
    Fraction(4): "whole",
    Fraction(2): "half",
    Fraction(1): "quarter",
    Fraction(1, 2): "eighth",
    Fraction(1, 4): "16th",
    Fraction(1, 8): "32nd",
    Fraction(1, 16): "64th",
    Fraction(1, 32): "128th",
}
DYNAMICS = {"p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sf", "sfz", "fp", "sfp", "sfpp", "rf", "rfz", "fz"}
ORNAMENTS = {"trill-mark", "mordent", "turn"}
TECHNICAL = {"up-bow", "down-bow"}


def _divisions(score: Score) -> int:
    den = 1
    for p in score.parts:
        for m in p.measures:
            for e in m.events:
                for v in (e.duration, m.expected or Fraction(0)):
                    den = den * v.denominator // math.gcd(den, v.denominator)
    while den < 4:
        den *= 2
    return den


def _is_power_of_two(fr: Fraction) -> bool:
    if fr <= 0:
        return False
    n, d = fr.numerator, fr.denominator
    return (n & (n - 1) == 0) and (d & (d - 1) == 0)


class _Writer:
    def __init__(self, score: Score):
        self.score = score
        self.div = _divisions(score)
        self.out: list[str] = []

    def w(self, s: str) -> None:
        self.out.append(s)

    def dur(self, q: Fraction) -> int:
        return int(q * self.div)

    def run(self) -> str:
        sc = self.score
        self.w('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
        self.w('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
               '"http://www.musicxml.org/dtds/partwise.dtd">')
        self.w('<score-partwise version="4.0">')
        if sc.title:
            self.w(f"  <work><work-title>{escape(sc.title)}</work-title></work>")
        self.w("  <identification>")
        for kind, val in (("composer", sc.composer), ("lyricist", sc.lyricist), ("arranger", sc.arranger)):
            if val:
                self.w(f'    <creator type="{kind}">{escape(val)}</creator>')
        self.w("    <encoding>")
        self.w(f"      <software>sheet2xml {__version__}</software>")
        self.w('      <supports element="accidental" type="yes"/>')
        self.w('      <supports element="beam" type="no"/>')
        self.w('      <supports element="stem" type="yes"/>')
        self.w("    </encoding>")
        self.w("  </identification>")
        if sc.title or sc.subtitle:
            self.w("  <credit page=\"1\"><credit-type>title</credit-type>"
                   f"<credit-words justify=\"center\" valign=\"top\" font-size=\"22\">{escape(sc.title)}</credit-words></credit>")
        if sc.subtitle:
            self.w("  <credit page=\"1\"><credit-type>subtitle</credit-type>"
                   f"<credit-words justify=\"center\" valign=\"top\">{escape(sc.subtitle)}</credit-words></credit>")
        right = "\n".join(x for x in (sc.composer, sc.arranger) if x)
        if right:
            self.w("  <credit page=\"1\"><credit-type>composer</credit-type>"
                   f"<credit-words justify=\"right\" valign=\"top\">{escape(right)}</credit-words></credit>")
        if sc.lyricist:
            self.w("  <credit page=\"1\"><credit-type>lyricist</credit-type>"
                   f"<credit-words justify=\"left\" valign=\"top\">{escape(sc.lyricist)}</credit-words></credit>")
        self.w("  <part-list>")
        for i, p in enumerate(sc.parts, start=1):
            self.w(f'    <score-part id="P{i}">')
            self.w(f"      <part-name>{escape(p.name)}</part-name>")
            if p.abbreviation:
                self.w(f"      <part-abbreviation>{escape(p.abbreviation)}</part-abbreviation>")
            self.w("    </score-part>")
        self.w("  </part-list>")
        for i, p in enumerate(sc.parts, start=1):
            self.part(i, p)
        self.w("</score-partwise>")
        return "\n".join(self.out) + "\n"

    # ------------------------------------------------------------------ parts
    def part(self, idx: int, p: Part) -> None:
        self.w(f'  <part id="P{idx}">')
        self.clef_now: dict[int, object] = {}
        pickup = False
        if p.measures:
            m0 = p.measures[0]
            total = _voice_len(m0)
            pickup = bool(m0.expected and 0 < total < m0.expected and not any(e.full_measure for e in m0.events))
        for mi, m in enumerate(p.measures):
            # implicit: the second half of a split measure is not counted by the notation program
            number = mi if pickup else mi + 1
            self.measure(p, m, mi, number, implicit=(pickup and mi == 0) or m.implicit)
        self.w("  </part>")

    def measure(self, p: Part, m: Measure, mi: int, number: int, implicit: bool) -> None:
        attrs = ' implicit="yes"' if implicit else ""
        self.w(f'    <measure number="{number}"{attrs}>')
        att: list[str] = []
        if mi == 0:
            att.append(f"<divisions>{self.div}</divisions>")
        if m.key is not None or mi == 0:
            att.append(f"<key><fifths>{m.key or 0}</fifths></key>")
        if m.time is not None:
            sym = f' symbol="{m.time_symbol}"' if m.time_symbol in ("common", "cut") else ""
            att.append(f"<time{sym}><beats>{m.time[0]}</beats><beat-type>{m.time[1]}</beat-type></time>")
        elif mi == 0:
            att.append("<time><beats>4</beats><beat-type>4</beat-type></time>")
        if mi == 0 and p.staves > 1:
            att.append(f"<staves>{p.staves}</staves>")
        for s in sorted(m.clefs):
            c = m.clefs[s]
            if mi > 0 and self.clef_now.get(s) == c:
                continue  # the clef printed at every system start is not a change
            self.clef_now[s] = c
            num = f' number="{s}"' if p.staves > 1 else ""
            oc = f"<clef-octave-change>{c.octave_change}</clef-octave-change>" if c.octave_change else ""
            line = "" if c.sign == "percussion" else f"<line>{c.line}</line>"
            att.append(f"<clef{num}><sign>{c.sign}</sign>{line}{oc}</clef>")
        if mi == 0 and p.transpose:
            d, c = p.transpose
            att.append(f"<transpose><diatonic>{d}</diatonic><chromatic>{c}</chromatic></transpose>")
        if att:
            self.w("      <attributes>" + "".join(att) + "</attributes>")

        if m.repeat_start:
            self.w('      <barline location="left"><bar-style>heavy-light</bar-style>'
                   '<repeat direction="forward"/></barline>')
        voices = sorted({e.voice for e in m.events}) or [1]
        expected = m.expected or Fraction(4)
        full = max([_voice_len(m, v) for v in voices] + [Fraction(0)])
        measure_len = full if (implicit or m.partial) and full else max(expected, full)
        pending_dirs = sorted(m.directions, key=lambda d: d.x)
        pending_clefs = sorted(m.clef_changes, key=lambda c: c[0])
        first_voice_of_staff: dict[int, int] = {}
        for v in voices:
            evs = [e for e in m.events if e.voice == v]
            if evs:
                first_voice_of_staff.setdefault(evs[0].staff, v)
        for vi, v in enumerate(voices):
            evs = sorted([e for e in m.events if e.voice == v], key=lambda e: e.x)
            if vi > 0:
                self.w(f"      <backup><duration>{self.dur(measure_len)}</duration></backup>")
            pos = Fraction(0)
            tuplet_acc: list[Fraction] = []
            for ei, e in enumerate(evs):
                if first_voice_of_staff.get(e.staff) == v:
                    while pending_clefs and pending_clefs[0][1] == e.staff and pending_clefs[0][0] <= e.x + 0.5:
                        _, s, c = pending_clefs.pop(0)
                        if self.clef_now.get(s) == c:
                            continue
                        self.clef_now[s] = c
                        num = f' number="{s}"' if p.staves > 1 else ""
                        line = "" if c.sign == "percussion" else f"<line>{c.line}</line>"
                        self.w(f"      <attributes><clef{num}><sign>{c.sign}</sign>{line}</clef></attributes>")
                    keep = []
                    for d in pending_dirs:
                        if d.staff == e.staff and d.x <= e.x + 2:
                            self.direction(d, p.staves)
                        else:
                            keep.append(d)
                    pending_dirs = keep
                tstart = tstop = False
                if e.tuplet and not e.grace:
                    if not tuplet_acc:
                        tstart = True
                    tuplet_acc.append(_notated(e))
                    # the group closes once it spans n equal units (and not before every
                    # note of the smallest value could have filled it: sextuplets, dotted pairs)
                    total = sum(tuplet_acc)
                    if _is_power_of_two(total / e.tuplet[0]) and total >= e.tuplet[0] * min(tuplet_acc):
                        tstop = True
                        tuplet_acc = []
                elif tuplet_acc:
                    tuplet_acc = []
                self.event(e, p.staves, tstart, tstop)
                pos += e.duration
            if pos < measure_len:
                self.w(f"      <forward><duration>{self.dur(measure_len - pos)}</duration></forward>")
        for d in pending_dirs:
            self.direction(d, p.staves)
        if m.barline == "repeat-end":
            self.w('      <barline location="right"><bar-style>light-heavy</bar-style>'
                   '<repeat direction="backward"/></barline>')
        elif m.barline:
            style = {"final": "light-heavy", "double": "light-light"}.get(m.barline, "regular")
            self.w(f'      <barline location="right"><bar-style>{style}</bar-style></barline>')
        self.w("    </measure>")

    def direction(self, d, staves: int) -> None:
        place = "above" if d.above else "below"
        if d.kind == "dynamic" and d.text in DYNAMICS:
            body = f"<dynamics><{d.text}/></dynamics>"
        else:
            body = f"<words>{escape(d.text)}</words>"
        staff = f"<staff>{d.staff}</staff>" if staves > 1 else ""
        self.w(f'      <direction placement="{place}"><direction-type>{body}</direction-type>{staff}</direction>')

    def event(self, e: Event, staves: int, tstart: bool, tstop: bool) -> None:
        tname = TYPE_NAMES.get(e.base)
        staff = f"<staff>{e.staff}</staff>" if staves > 1 else ""
        tm = ""
        if e.tuplet:
            tm = (f"<time-modification><actual-notes>{e.tuplet[0]}</actual-notes>"
                  f"<normal-notes>{e.tuplet[1]}</normal-notes></time-modification>")
        if e.is_rest:
            hidden = ' print-object="no"' if e.hidden else ""
            measure_attr = ' measure="yes"' if e.full_measure else ""
            parts = [f"      <note{hidden}><rest{measure_attr}/><duration>{self.dur(e.duration)}</duration>",
                     f"<voice>{e.voice}</voice>"]
            if tname and not e.full_measure:
                parts.append(f"<type>{tname}</type>" + "<dot/>" * e.dots)
            parts.append(tm)
            parts.append(staff)
            notations = self._tuplet_notation(e, tstart, tstop)
            if notations:
                parts.append(f"<notations>{notations}</notations>")
            parts.append("</note>")
            self.w("".join(parts))
            return
        for i, pt in enumerate(e.pitches):
            xml = ["      <note>"]
            if e.grace:
                xml.append('<grace slash="yes"/>' if e.base <= Fraction(1, 2) else "<grace/>")
            if i > 0:
                xml.append("<chord/>")
            alter = f"<alter>{pt.alter}</alter>" if pt.alter else ""
            xml.append(f"<pitch><step>{pt.step}</step>{alter}<octave>{pt.octave}</octave></pitch>")
            if not e.grace:
                xml.append(f"<duration>{self.dur(e.duration)}</duration>")
            if pt.tie_stop:
                xml.append('<tie type="stop"/>')
            if pt.tie_start:
                xml.append('<tie type="start"/>')
            xml.append(f"<voice>{e.voice}</voice>")
            if tname:
                xml.append(f"<type>{tname}</type>")
            xml.append("<dot/>" * e.dots)
            acc = getattr(pt, "_shown", None)
            if acc:
                xml.append(f"<accidental>{acc}</accidental>")
            xml.append(tm)
            if e.stem:
                xml.append(f"<stem>{e.stem}</stem>")
            if e.notehead:
                xml.append(f"<notehead>{e.notehead}</notehead>")
            xml.append(staff)
            nota = ""
            if pt.tie_stop:
                nota += '<tied type="stop"/>'
            if pt.tie_start:
                nota += '<tied type="start"/>'
            if i == 0:
                nota += self._tuplet_notation(e, tstart, tstop)
                groups = {"ornaments": ORNAMENTS, "technical": TECHNICAL}
                for tag, names in groups.items():
                    marks = [a for a in e.articulations if a in names]
                    if marks:
                        nota += f"<{tag}>" + "".join(f"<{a}/>" for a in marks) + f"</{tag}>"
                arts = [a for a in e.articulations if a != "fermata" and a not in ORNAMENTS | TECHNICAL]
                if arts:
                    nota += "<articulations>" + "".join(f"<{a}/>" for a in arts) + "</articulations>"
                if "fermata" in e.articulations:
                    nota += '<fermata type="upright"/>'
            if nota:
                xml.append(f"<notations>{nota}</notations>")
            xml.append("</note>")
            self.w("".join(xml))

    @staticmethod
    def _tuplet_notation(e: Event, tstart: bool, tstop: bool) -> str:
        if not e.tuplet:
            return ""
        s = ""
        if tstart:
            s += '<tuplet type="start" bracket="no"/>'
        if tstop:
            s += '<tuplet type="stop"/>'
        return s


def _notated(e: Event) -> Fraction:
    d = e.base
    add = e.base
    for _ in range(e.dots):
        add /= 2
        d += add
    return d


def _voice_len(m: Measure, voice: int | None = None) -> Fraction:
    if voice is None:
        voices = {e.voice for e in m.events}
        return max((_voice_len(m, v) for v in voices), default=Fraction(0))
    return sum((e.duration for e in m.events if e.voice == voice), Fraction(0))


def to_musicxml(score: Score) -> str:
    return _Writer(score).run()
