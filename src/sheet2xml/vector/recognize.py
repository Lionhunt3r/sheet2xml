"""Turn the glyphs and shapes of one system into measures full of events.

Coordinates are PDF points with y growing downwards. ``sp`` is the distance
between two staff lines; most tolerances are expressed in it.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from fractions import Fraction

from ..model import Clef, Direction, Event, Measure, Pitch
from .extract import Curve, Glyph, PageContent, Polygon, Segment, Text
from .layout import Staff, System

# chord symbols (C, F#m7, Bbmaj7/D, Fmin, n.c.) and playing instructions are not lyrics
CHORD_NAME = re.compile(r"^([A-H][#b♯♭]?(m|min|maj|dim|aug|sus|add|M|°|ø|\+|-)?[0-9]*(\([^)]*\))?"
                        r"(/[A-H][#b♯♭]?)?|n\.?c\.?|N\.?C\.?)$")
NOT_LYRICS = {
    "pizz", "arco", "div", "unis", "tutti", "solo", "soli", "trem", "col legno", "sul", "con sord", "senza sord",
    "rit", "ritard", "rall", "accel", "a tempo", "cresc", "decresc", "dim", "simile", "legato", "stacc", "ten",
    "espr", "dolce", "marc", "sempre", "poco", "molto", "subito", "sub", "fine", "coda", "segno", "tacet",
    "1st only", "2nd only", "only", "1x", "2x", "opt", "ad lib", "gliss", "harm", "mute", "open", "loco",
}

HEAD_VALUE = {
    "head.breve": Fraction(8),
    "head.whole": Fraction(4),
    "head.half": Fraction(2),
    "head.black": Fraction(1),
    "head.slash": Fraction(1),
    "head.slash.half": Fraction(2),
    "head.slash.whole": Fraction(4),
}
REST_VALUE = {
    "rest.breve": Fraction(8),
    "rest.whole": Fraction(4),
    "rest.half": Fraction(2),
    "rest.quarter": Fraction(1),
    "rest.8": Fraction(1, 2),
    "rest.16": Fraction(1, 4),
    "rest.32": Fraction(1, 8),
    "rest.64": Fraction(1, 16),
}
ACC_ALTER = {"acc.sharp": 1, "acc.flat": -1, "acc.natural": 0, "acc.dsharp": 2, "acc.dflat": -2}
SHARP_ORDER = "FCGDAEB"
STEPS = "CDEFGAB"


@dataclass
class Head:
    glyph: Glyph
    pos: int  # staff position: 0 = bottom line, 1 = first space, ...
    grace: bool = False
    accidental: int | None = None
    stem: Segment | None = None
    tie_start: bool = False
    tie_stop: bool = False


ARTICULATION = {
    "art.accent": "accent",
    "art.tenuto": "tenuto",
    "art.staccato": "staccato",
    "art.fermata": "fermata",
    "art.staccatissimo": "staccatissimo",
    "art.marcato": "strong-accent",
    "art.upbow": "up-bow",
    "art.downbow": "down-bow",
    "orn.trill": "trill-mark",
    "orn.mordent": "mordent",
    "orn.turn": "turn",
}


@dataclass
class Chord:
    heads: list[Head]
    x: float
    stem: Segment | None
    direction: str | None  # "up" / "down"
    tip: float | None  # y of the free stem end
    value: Fraction  # notated value
    dots: int = 0
    grace: bool = False
    tuplet: tuple[int, int] | None = None
    beams: list[int] = field(default_factory=list)  # ids of beams attached
    voice: int = 1
    articulations: list[str] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(h.glyph.y for h in self.heads)

    @property
    def bottom(self) -> float:
        return max(h.glyph.y for h in self.heads)


@dataclass
class RestSym:
    glyph: Glyph
    value: Fraction
    dots: int = 0
    tuplet: tuple[int, int] | None = None
    voice: int = 1

    @property
    def x(self) -> float:
        return self.glyph.x


@dataclass
class StaffContent:
    staff: Staff
    glyphs: list[Glyph] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    clef: Clef | None = None
    clef_x1: float | None = None
    key: int | None = None
    time: tuple[int, int] | None = None
    time_symbol: str | None = None
    header_end: float = 0.0
    measures: list[Measure] = field(default_factory=list)
    label: str = ""


def _clef_from_glyph(g: Glyph, st: Staff) -> Clef:
    line = round((st.bottom - g.y) / st.space) + 1
    line = max(1, min(5, line))
    if g.name == "clef.F":
        return Clef("F", line if line in (3, 4, 5) else 4)
    if g.name == "clef.C":
        return Clef("C", line)
    if g.name == "clef.perc":
        return Clef("percussion", 3)
    if g.name == "clef.G8vb":
        return Clef("G", 2, -1)
    if g.name == "clef.G8va":
        return Clef("G", 2, 1)
    return Clef("G", line if line in (1, 2) else 2)


def _clef_reference(clef: Clef) -> int:
    """Diatonic number (octave*7+step) of the bottom staff line."""
    if clef.sign == "F":
        ref = 3 * 7 + STEPS.index("F")  # F3 on the clef line
    elif clef.sign == "C":
        ref = 4 * 7 + STEPS.index("C")
    elif clef.sign == "percussion":
        ref = 4 * 7 + STEPS.index("B")  # middle line B4 like treble clef
        return ref - 4
    else:
        ref = 4 * 7 + STEPS.index("G")
    ref += 7 * clef.octave_change
    return ref - 2 * (clef.line - 1)


def _key_alter(key: int, step: str) -> int:
    if key > 0 and step in SHARP_ORDER[:key]:
        return 1
    if key < 0 and step in SHARP_ORDER[::-1][: -key]:
        return -1
    return 0


class SystemReader:
    def __init__(self, pc: PageContent, system: System, carry: dict[int, dict], page_staves: list[Staff] | None = None):
        self.pc = pc
        self.system = system
        # staves of the other systems on the page, so that symbols near them are left alone
        own = {id(s) for s in system.staves}
        self.other_staves = [s for s in (page_staves or []) if id(s) not in own]
        self.carry = carry  # per staff position in system order: last clef/key/time
        self.sp = sum(s.space for s in system.staves) / len(system.staves)
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ setup
    def _assign(self) -> list[StaffContent]:
        sy = self.system
        contents = [StaffContent(staff=s) for s in sy.staves]
        everything = contents + [StaffContent(staff=s) for s in self.other_staves]
        top_limit = sy.top - 8 * self.sp
        bottom_limit = sy.bottom + 8 * self.sp
        ledgers = [
            s for s in self.pc.segments
            if s.is_horizontal and abs(s.x1 - s.x0) < 4 * self.sp and sy.x0 <= min(s.x0, s.x1) <= sy.x1
        ]
        for g in self.pc.glyphs:
            if g.x < sy.x0 - 3 * self.sp or g.x > sy.x1 + 2 * self.sp:
                if g.name != "brace":
                    continue
            if not (top_limit <= g.y <= bottom_limit):
                continue
            best = min(everything, key=lambda c: (c.staff.distance(g.y), abs(g.y - c.staff.middle)))
            if best.staff.distance(g.y) > 7 * best.staff.space:
                continue
            if g.name.startswith("head.") and best.staff.distance(g.y) > 1.2 * best.staff.space:
                best = self._ledger_owner(g, everything, ledgers) or best
            best.glyphs.append(g)
        for t in self.pc.texts:
            if not (top_limit <= t.origin_y <= bottom_limit):
                continue
            best = min(everything, key=lambda c: (c.staff.distance(t.origin_y), abs(t.origin_y - c.staff.middle)))
            best.texts.append(t)
        return contents

    def _ledger_owner(self, g: Glyph, contents: list[StaffContent], ledgers: list[Segment]):
        """A note between two staves belongs to the staff its ledger lines lead to."""
        above = [c for c in contents if c.staff.bottom < g.y]
        below = [c for c in contents if c.staff.top > g.y]
        cands = []
        if above:
            cands.append((max(above, key=lambda c: c.staff.bottom), "above"))
        if below:
            cands.append((min(below, key=lambda c: c.staff.top), "below"))
        best, best_n = None, 0
        cx = g.x + 0.6 * self.sp
        for c, side in cands:
            st = c.staff
            if side == "above":
                lo, hi = st.bottom + 0.5 * st.space, g.y - 0.3 * st.space
            else:
                lo, hi = g.y + 0.3 * st.space, st.top - 0.5 * st.space
            ys = [
                s.y0 for s in ledgers
                if min(s.x0, s.x1) - 0.3 * st.space <= cx <= max(s.x0, s.x1) + 0.3 * st.space
                and lo <= s.y0 <= hi
            ]
            # the ledger lines must lead from the staff to the note; lines far from
            # either end belong to other notes
            edge = st.bottom if side == "above" else st.top
            reach = any(abs(y - g.y) < 1.1 * st.space for y in ys) and any(abs(y - edge) < 1.3 * st.space for y in ys)
            n = len(ys) if reach else 0
            if n > best_n or (n == best_n and n and best is not None and st.distance(g.y) < best.staff.distance(g.y)):
                best, best_n = c, n
        return best

    def _segments_in(self, st: Staff) -> list[Segment]:
        lo = st.top - 6 * st.space
        hi = st.bottom + 6 * st.space
        out = []
        for s in self.pc.segments:
            if not s.is_vertical:
                continue
            y0, y1 = min(s.y0, s.y1), max(s.y0, s.y1)
            if y1 < lo or y0 > hi or y1 - y0 < 1.5 * st.space:
                continue
            if s.x0 < st.x0 - st.space or s.x0 > st.x1 + st.space:
                continue
            out.append(s)
        return out

    def _beams_in(self, st: Staff) -> list[Polygon]:
        out = []
        for p in self.pc.polygons:
            if p.x1 < st.x0 or p.x0 > st.x1:
                continue
            if p.y1 < st.top - 8 * st.space or p.y0 > st.bottom + 8 * st.space:
                continue
            w = p.x1 - p.x0
            if w < 0.8 * st.space or len(p.points) > 6:
                continue
            th = _thickness(p)
            if 0.25 * st.space <= th <= 0.8 * st.space:
                out.append(p)
        return out

    # ------------------------------------------------------------------ main
    def read(self) -> list[StaffContent]:
        contents = self._assign()
        bar_xs = [b for b in self.system.barlines]
        for i, sc in enumerate(contents):
            self._read_staff(i, sc, bar_xs)
        return contents

    def _read_staff(self, idx: int, sc: StaffContent, barlines) -> None:
        st = sc.staff
        sp = st.space
        carry = self.carry.setdefault(idx, {})
        glyphs = sorted(sc.glyphs, key=lambda g: g.x)
        normal = _median([g.size for g in glyphs if g.name.startswith("head.")]) or 4 * sp
        # ---- header: clef, key, time
        clefs = [g for g in glyphs if g.name.startswith("clef.")]
        first_content_x = min(
            (g.x for g in glyphs if g.name.startswith(("head.", "rest."))), default=st.x1
        )
        header_clef = next((c for c in clefs if c.x < st.x0 + 4 * sp), None)
        if header_clef:
            sc.clef = _clef_from_glyph(header_clef, st)
            sc.clef_x1 = header_clef.x1
        clef = sc.clef or carry.get("clef") or Clef("G", 2)
        x_after = (header_clef.x1 if header_clef else st.x0) + 0.2 * sp
        accs = [g for g in glyphs if g.name.startswith("acc.")]
        heads = [g for g in glyphs if g.name.startswith("head.")]
        # key signatures stay within the staff (at most a space above or below it)
        key_cands = [a for a in accs if st.top - 1.2 * sp <= a.y <= st.bottom + 1.2 * sp]
        key_accs, x_after = self._key_signature(key_cands, heads, x_after, first_content_x, sp)
        used_ids: set[int] = {id(g) for g in key_accs}
        if key_accs:
            sc.key = _fifths(key_accs)
        elif header_clef is not None:
            sc.key = 0  # a system that opens without a key signature is in C major / A minor
        time_digits = [
            g for g in glyphs
            if g.name.startswith("time.") and st.top - 0.6 * sp <= g.y <= st.bottom + 0.6 * sp
            and g.size >= 0.7 * normal
        ]
        header_time = [g for g in time_digits if g.x < first_content_x and g.x < x_after + 8 * sp]
        if header_time:
            sc.time, sc.time_symbol = _parse_time(header_time, st)
            used_ids |= {id(g) for g in header_time}
        key = sc.key if sc.key is not None else carry.get("key", 0)
        time = sc.time or carry.get("time") or (4, 4)
        time_symbol = sc.time_symbol if sc.time else carry.get("time_symbol")
        if sc.clef:
            carry["clef"] = sc.clef
        if sc.key is not None:
            carry["key"] = sc.key
        if sc.time:
            carry["time"] = sc.time
            carry["time_symbol"] = sc.time_symbol

        # ---- measure regions
        # split at the right line of double and final barlines: a crowded note
        # may sit between the two lines but always belongs to the measure before
        bxs = sorted(b[1] for b in barlines if b[0] > st.x0 + 1.5 * sp)
        bkinds = {round(b[1], 1): b[2] for b in barlines}
        bar_lines_x = [x for b in barlines for x in (b[0], b[1])]
        edges = [st.x0] + bxs
        if not bxs or bxs[-1] < st.x1 - 2 * sp:
            edges.append(st.x1 + 0.5 * sp)
        regions = list(zip(edges[:-1], edges[1:]))

        segs = self._segments_in(st)
        beams = self._beams_in(st)
        stems = [s for s in segs if not any(abs(s.x0 - b) < 0.3 * sp for b in bar_lines_x)]

        repeat_end, repeat_start = self._repeats(st, glyphs, barlines, used_ids)
        chords, rests = self._events(st, glyphs, stems, beams, normal, used_ids)
        self._ties(st, chords)
        self._tuplets(st, sc.texts, glyphs, chords, rests, beams, segs, used_ids)

        # mid-staff clef / key / time changes keyed by x
        clef_changes = [
            (g.x, _clef_from_glyph(g, st)) for g in clefs if g is not header_clef and g.x > st.x0 + 4 * sp
        ]
        time_changes = []
        rest_digits = [g for g in time_digits if id(g) not in used_ids]
        for grp in _cluster_x(rest_digits, 1.5 * sp):
            if any(abs(c.x - grp[0].x) < 1.5 * sp for c in chords):
                continue
            t, sym = _parse_time(grp, st)
            if t:
                time_changes.append((min(g.x for g in grp), t, sym))
                used_ids |= {id(g) for g in grp}
        key_changes = []
        for bx in bxs:
            ka, _ = self._key_signature(
                [a for a in key_cands if id(a) not in used_ids], heads, bx + 0.2 * sp, st.x1, sp, max_start=2.5 * sp
            )
            if ka:
                key_changes.append((bx, _fifths(ka)))
                used_ids |= {id(a) for a in ka}
        multirests = self._multirests(st, glyphs, used_ids)

        # ---- accidentals (after key signatures are consumed)
        self._accidentals(st, [a for a in accs if id(a) not in used_ids], chords)

        directions = self._directions(st, glyphs, sc.texts)

        for mi, (a, b) in enumerate(regions):
            m = Measure(number=0)
            m.repeat_start = any(abs(x - a) < 0.5 * sp for x in repeat_start)
            if mi == 0 and carry.pop("repeat_start", False):
                m.repeat_start = True  # the previous system ended with a start-repeat barline
            in_region = lambda x: a - 0.2 * sp <= x < b - 0.2 * sp  # noqa: E731
            for x, k in key_changes:
                if abs(x - a) < 0.5 * sp:
                    key = k
                    m.key = k
            # a time change sits at the start of the measure, after any key change
            first = min((e.x for e in chords + rests if in_region(e.x)), default=b)
            for x, t, sym in time_changes:
                if in_region(x) and x < min(first, a + 14 * sp):
                    time, time_symbol = t, sym
                    m.time = t
                    m.time_symbol = sym
            if mi == 0:
                if sc.key is not None:
                    m.key = sc.key
                if sc.time:
                    m.time = sc.time
                    m.time_symbol = sc.time_symbol
                m.clefs[1] = clef
            for x, c in clef_changes:
                if in_region(x):
                    m.clef_changes.append((x, 1, c))
            m_chords = [c for c in chords if in_region(c.x)]
            m_rests = [r for r in rests if in_region(r.x)]
            expected = Fraction(time[0] * 4, time[1])
            m.expected = expected
            mr = [n for x, n in multirests if in_region(x)]
            if mr and not m_chords:
                m.rest_measures = mr[0]
                m_rests = []
            m.events = self._build_events(st, m_chords, m_rests, clef, key, m.clef_changes, expected, m)
            m.directions = [d for d in directions if in_region(d.x)]
            kind = bkinds.get(round(b, 1)) if b in bxs else None
            if kind in ("final", "double", "heavy"):
                m.barline = "final" if kind in ("final", "heavy") else "double"
            if any(abs(x - b) < 0.5 * sp for x in repeat_end):
                m.barline = "repeat-end"
            m._geom = (self.pc.number, a, b, st.top, st.bottom, st.space, clef)  # type: ignore[attr-defined]
            m._time = time  # type: ignore[attr-defined]
            m._time_symbol = time_symbol  # type: ignore[attr-defined]
            m._key = key  # type: ignore[attr-defined]
            if m.clef_changes:
                clef = m.clef_changes[-1][2]
            sc.measures.append(m)
        if any(x > st.x1 - 0.5 * sp for x in repeat_start):
            carry["repeat_start"] = True
        carry["clef"] = clef
        carry["key"] = key
        carry["time"] = time
        carry["time_symbol"] = time_symbol
        # drop trailing regions that only hold courtesy signatures
        while len(sc.measures) > 1 and not sc.measures[-1].events and regions[-1][1] > st.x1:
            sc.measures.pop()
        sc.header_end = x_after
        self._lyrics(st, sc)

    def _lyrics(self, st: Staff, sc: StaffContent) -> None:
        """Lyrics: lines of upright text below the staff whose syllables sit under its notes."""
        sp = st.space
        notes = [e for m in sc.measures for e in m.events if not e.is_rest and not e.grace]
        if not notes:
            return
        below = [o.top for o in self.system.staves + self.other_staves if o.top > st.bottom + sp]
        limit = min(below + [st.bottom + 14 * sp])
        tokens = []
        for t in self.pc.texts:
            if not (st.bottom + 0.8 * sp < t.origin_y < limit) or t.italic or t.bold:
                continue
            if not (st.x0 - sp < t.cx < st.x1 + sp):
                continue
            for w, x0, x1 in t.words or [(t.text, t.x0, t.x1)]:
                tokens.append((t.origin_y, x0, x1, w))
        tokens.sort()
        lines: list[list[tuple]] = []
        for tok in tokens:
            if lines and abs(tok[0] - lines[-1][0][0]) < 0.4 * sp:
                lines[-1].append(tok)
            else:
                lines.append([tok])
        verse = 0
        last_y = 0.0
        low = max([st.bottom] + [g.y for g in sc.glyphs if g.name.startswith("head.")])
        for line in lines:
            line.sort(key=lambda t: t[1])
            words = [w for *_, w in line if w.strip("-–_")]
            if not words or sum(1 for w in words if CHORD_NAME.match(w) or w.lower().strip(".") in NOT_LYRICS) * 2 >= len(words):
                continue  # chord symbols over the next staff, playing instructions
            sylls: list[list] = []  # [text, x0, x1, continues]
            for _, x0, x1, w in line:
                if w.strip("-–_") == "":
                    if "-" in w or "–" in w:
                        if sylls:
                            sylls[-1][3] = True
                    continue
                cont = w.endswith(("-", "–"))
                sylls.append([w.strip("-–"), x0, x1, cont])
            matched = []
            used: set[int] = set()
            for text, x0, x1, cont in sylls:
                c = (x0 + x1) / 2
                cands = [e for e in notes if id(e) not in used and abs(e.x + 0.55 * sp - c) < 2.5 * sp]
                if not cands:
                    continue
                e = min(cands, key=lambda e: (round(abs(e.x + 0.55 * sp - c) / (0.4 * sp)), e.voice))
                used.add(id(e))
                matched.append((e, text + ("-" if cont else "")))
            # the first verse sits close under the staff (or its lowest notes), the next ones
            # right under the previous verse
            if line[0][0] - (last_y if verse else low) > (3.5 if verse else 6.5) * sp:
                continue
            # a single syllable counts only right under an accepted verse (end of a second verse)
            alone = len(matched) == len(sylls) == 1 and verse
            if (len(matched) < 2 and not alone) or len(matched) < 0.6 * len(sylls):
                continue
            verse += 1
            last_y = line[0][0]
            for e, text in matched:
                while len(e.lyrics) < verse:
                    e.lyrics.append("")
                e.lyrics[verse - 1] = text

    # ------------------------------------------------------------------ pieces
    def _key_signature(self, accs, heads, x_start, limit, sp, max_start=4.0 * 4):
        seq = []
        cur = x_start
        for a in sorted(accs, key=lambda g: g.x):
            if a.x < x_start - 0.2 * sp or a.x > limit:
                continue
            if a.name not in ("acc.sharp", "acc.flat", "acc.natural"):
                break
            gap_limit = (max_start if not seq else 1.8 * sp)
            if a.x - cur > gap_limit:
                break
            attached = any(
                abs(h.y - a.y) < 0.3 * sp and 0 < h.x - a.x1 < 1.6 * sp for h in heads
            )
            if attached:
                break
            seq.append(a)
            cur = a.x1
        # a key signature never repeats the same line twice
        ys = [round(a.y / (sp / 2)) for a in seq if a.name != "acc.natural"]
        if len(set(ys)) != len(ys):
            return [], x_start
        if seq and all(a.name == "acc.natural" for a in seq):
            return seq, cur
        return seq, (cur if seq else x_start)

    def _events(self, st, glyphs, stems, beams, normal, used_ids):
        sp = st.space
        heads: list[Head] = []
        for g in glyphs:
            if not g.name.startswith("head."):
                continue
            pos = round((st.bottom - g.y) / (sp / 2))
            if g.name.startswith("head.slash"):
                pos = 4  # rhythm slashes sit on the middle line; glyph origins vary by font
            heads.append(Head(glyph=g, pos=pos, grace=g.size < 0.8 * normal))
        # attach stems
        by_stem: dict[int, list[Head]] = {}
        stemless: list[Head] = []
        for h in heads:
            g = h.glyph
            hw = (g.x1 - g.x0) if g.x1 - g.x0 < 2.2 * sp else 1.2 * sp
            cands = []
            for s in stems:
                sx = s.x0
                y0, y1 = min(s.y0, s.y1), max(s.y0, s.y1)
                if not (g.x - 0.45 * sp <= sx <= g.x + hw + 0.6 * sp):
                    continue
                if not (y0 - 0.6 * sp <= g.y <= y1 + 0.6 * sp):
                    continue
                cands.append((min(abs(sx - g.x), abs(sx - (g.x + hw))), s))
            cands.sort(key=lambda t: t[0])
            best = cands[0][1] if cands else None
            if len(cands) > 1:
                # a unison of two voices is drawn as two identical heads, one per stem
                twin = any(o.pos == h.pos and abs(o.glyph.x - g.x) < 0.3 * sp
                           for o in by_stem.get(id(best), []))
                other = cands[1][1]

                def goes_up(s) -> bool:
                    return (min(s.y0, s.y1) + max(s.y0, s.y1)) / 2 < g.y

                if twin and goes_up(other) != goes_up(best):
                    best = other
            if best is not None and g.name not in ("head.whole", "head.breve", "head.slash.whole"):
                h.stem = best
                by_stem.setdefault(id(best), []).append(h)
            else:
                stemless.append(h)
        chords: list[Chord] = []
        for hs in by_stem.values():
            s = hs[0].stem
            y0, y1 = min(s.y0, s.y1), max(s.y0, s.y1)
            mean = sum(h.glyph.y for h in hs) / len(hs)
            direction = "up" if abs(mean - y1) < abs(mean - y0) else "down"
            tip = y0 if direction == "up" else y1
            x = min(h.glyph.x for h in hs)
            value = min(HEAD_VALUE[h.glyph.name] for h in hs)
            chords.append(Chord(heads=hs, x=x, stem=s, direction=direction, tip=tip, value=value,
                                grace=all(h.grace for h in hs)))
        stemless.sort(key=lambda h: h.glyph.x)
        for h in stemless:
            for c in chords:
                if c.stem is None and abs(c.x - h.glyph.x) < 0.5 * sp:
                    c.heads.append(h)
                    break
            else:
                chords.append(Chord(heads=[h], x=h.glyph.x, stem=None, direction=None, tip=None,
                                    value=HEAD_VALUE[h.glyph.name], grace=h.grace))
        # flags and beams
        flags = [g for g in glyphs if g.name.startswith("flag.")]
        for c in chords:
            if c.stem is None or c.value != 1:
                continue
            sx = c.stem.x0
            n_flags = 0
            for f in flags:
                if abs(f.x - sx) < 0.6 * sp and abs(f.y - c.tip) < 3.6 * sp:
                    n_flags += int(f.name.rsplit(".", 1)[1])
            n_beams = 0
            for bi, b in enumerate(beams):
                if not (b.x0 - 0.3 * sp <= sx <= b.x1 + 0.3 * sp):
                    continue
                yb = _beam_y(b, sx)
                # distance from the stem tip towards the notehead
                dist = (yb - c.tip) if c.direction == "up" else (c.tip - yb)
                if -0.9 * sp <= dist <= 3.2 * sp:
                    n_beams += 1
                    c.beams.append(bi)
            n = max(n_flags, n_beams)
            if n:
                c.value = Fraction(1, 2 ** n)
        # rests
        rests = [
            RestSym(glyph=g, value=REST_VALUE[g.name])
            for g in glyphs
            if g.name in REST_VALUE and id(g) not in used_ids
            and st.top - 3 * sp <= g.y <= st.bottom + 3 * sp
        ]
        # dots
        dots = [g for g in glyphs if g.name == "dot" and id(g) not in used_ids]
        targets = [(c.x, c) for c in chords] + [(r.x, r) for r in rests]
        targets.sort(key=lambda t: t[0])
        heads_all = [(h.glyph, c) for c in chords for h in c.heads]

        def over_head(g: Glyph, far: float) -> Chord | None:
            """Chord whose notehead the glyph sits right above or below (articulation)."""
            gc = (g.x + g.x1) / 2
            best = None
            for hg, c in heads_all:
                dy = abs(hg.y - g.y)
                if abs((hg.x + hg.x1) / 2 - gc) < 0.6 * sp and 0.6 * sp < dy < far * sp:
                    if best is None or dy < best[0]:
                        best = (dy, c)
            return best[1] if best else None

        for g in glyphs:
            if g.name.startswith(("art.", "orn.")):
                c = over_head(g, 6.0)
                kind = ARTICULATION.get(g.name)
                if c is not None and kind and kind not in c.articulations:
                    c.articulations.append(kind)
        for d in dots:
            c = over_head(d, 4.0)
            if c is not None:  # staccato, not an augmentation dot
                if "staccato" not in c.articulations:
                    c.articulations.append("staccato")
                continue
            owner = None
            for x, t in targets:
                if x >= d.x:
                    break
                if d.x - x > 4.5 * sp:
                    continue
                if isinstance(t, Chord):
                    right = max(h.glyph.x1 for h in t.heads)
                    if d.x < right - 0.5 * sp:
                        continue
                    if any(abs(h.glyph.y - d.y) < (1.6 if h.glyph.name.startswith("head.slash") else 0.8) * sp
                           for h in t.heads):
                        owner = t
                else:
                    if t.glyph.x1 - 0.3 * sp <= d.x and abs(t.glyph.y - d.y) < 2.5 * sp:
                        owner = t
            if owner is None:
                continue
            owners = [owner]
            if isinstance(owner, Chord):
                # a head shared by two voices carries one dot for both
                pos = {h.pos for h in owner.heads}
                owners += [c for c in chords if c is not owner and abs(c.x - owner.x) < 0.3 * sp
                           and pos & {h.pos for h in c.heads} and c.value == owner.value]
            for o in owners:
                xs = getattr(o, "_dotx", [])
                if all(abs(d.x - x) > 0.5 * sp for x in xs):
                    xs.append(d.x)
                o._dotx = xs  # type: ignore[attr-defined]
        for t in [c for c in chords] + rests:
            t.dots = len(getattr(t, "_dotx", []))
        return chords, rests

    def _ties(self, st: Staff, chords: list[Chord]) -> None:
        sp = st.space
        heads = [(h, c) for c in chords for h in c.heads]
        seen = set()
        for cv in self.pc.curves:
            key = (round(cv.x0), round(cv.y0), round(cv.x1), round(cv.y1))
            if key in seen:
                continue
            seen.add(key)
            if cv.y1 < st.top - 6 * sp or cv.y0 > st.bottom + 6 * sp:
                continue
            if cv.x1 < st.x0 or cv.x0 > st.x1:
                continue
            width = cv.x1 - cv.x0
            if width < 0.8 * sp or (cv.y1 - cv.y0) > 2.5 * sp:
                continue
            ymid = (cv.y0 + cv.y1) / 2
            left = [
                (h, c) for h, c in heads
                if abs(h.glyph.y - ymid) < 1.4 * sp and -1.5 * sp <= cv.x0 - h.glyph.x <= 2.0 * sp
            ]
            right = [
                (h, c) for h, c in heads
                if abs(h.glyph.y - ymid) < 1.4 * sp and -2.0 * sp <= h.glyph.x - cv.x1 <= 1.0 * sp
            ]
            pairs = [
                (a, b) for a in left for b in right
                if a[0].pos == b[0].pos and b[0].glyph.x - a[0].glyph.x > 0.8 * sp
            ]
            if pairs:
                a, b = min(pairs, key=lambda p: abs(p[0][0].glyph.y - ymid))
                a[0].tie_start = True
                b[0].tie_stop = True
                continue
            if left and cv.x1 > st.x1 - 2.5 * sp:
                h = min(left, key=lambda p: abs(p[0].glyph.y - ymid))[0]
                h.tie_start = True
            elif right and cv.x0 < st.x0 + 8 * sp and not left:
                h = min(right, key=lambda p: abs(p[0].glyph.y - ymid))[0]
                h.tie_stop = True

    @staticmethod
    def _repeats(st: Staff, glyphs, barlines, used_ids: set[int]) -> tuple[list[float], list[float]]:
        """Barlines with repeat dots: (x of barlines ending a repeat, x of barlines starting one).

        Both are given as the right edge of the barline, like the measure regions. The dots
        are added to used_ids so they never count as augmentation dots."""
        sp = st.space
        dots = [
            g for g in glyphs
            if g.name in ("repeat.dot", "repeat.dots", "dot") and st.top + 1.2 * sp < g.y < st.top + 2.8 * sp
        ]

        def has_dots(lo: float, hi: float) -> bool:
            here = [g for g in dots if lo < g.x < hi]
            ys = {round((g.y - st.top) / sp * 2) for g in here}  # half spaces: 3 and 5
            if any(g.name == "repeat.dots" for g in here) or {3, 5} <= ys:
                used_ids.update(id(g) for g in here)
                return True
            return False

        end, start = [], []
        for x0, x1, _kind in barlines:
            if has_dots(x0 - 1.6 * sp, x0 - 0.05 * sp):
                end.append(x1)
            if has_dots(x1 + 0.05 * sp, x1 + 1.6 * sp):
                start.append(x1)
        return end, start

    def _rehearsal_mark(self, t, sp: float) -> bool:
        cx, cy = t.cx, (t.y0 + t.y1) / 2
        for shape in [*self.pc.curves, *self.pc.polygons]:
            if shape.x0 < cx < shape.x1 and shape.y0 < cy < shape.y1 and shape.x1 - shape.x0 < 4 * sp:
                return True
        return any(
            o is not t and abs(o.origin_y - t.origin_y) < 0.3 * sp and len(o.text) > 2
            and min(abs(o.x0 - t.x1), abs(t.x0 - o.x1)) < 3 * sp
            for o in self.pc.texts
        )

    def _tuplets(self, st, texts, glyphs, chords, rests, beams, segs, used_ids) -> None:
        sp = st.space
        numbers: list[tuple[float, float, int]] = []
        for g in glyphs:
            if g.name.startswith("tuplet."):
                numbers.append((g.cx, g.y, int(g.name.split(".")[1])))
        # tuplet numbers set in a text font (older Sibelius) or as small time-signature digits;
        # not when the page has real tuplet glyphs, and never rehearsal marks (boxed or
        # circled digits, or digits next to a tempo word)
        text_tuplets = not any(g.name.startswith("tuplet.") for g in self.pc.glyphs)
        for t in texts if text_tuplets else []:
            if t.text in ("2", "3", "4", "5", "6", "7") and t.size < 3.2 * sp and not self._rehearsal_mark(t, sp):
                numbers.append((t.cx, t.origin_y, int(t.text)))
        normal = _median([g.size for g in glyphs if g.name.startswith("head.")]) or 4 * sp
        for g in glyphs:
            if g.name in ("time.3", "time.5", "time.6") and g.size < 0.75 * normal and id(g) not in used_ids:
                if st.distance(g.y) > 0.5 * sp:
                    numbers.append((g.cx, g.y, int(g.name.split(".")[1])))
        # merge multi-digit numbers like "12" are rare for tuplets; ignore
        for x, y, n in numbers:
            normal_n = {2: 3, 3: 2, 4: 6, 5: 4, 6: 4, 7: 4, 9: 8}.get(n)
            if not normal_n:
                continue
            group: list = []
            for bi, b in enumerate(beams):
                if not b.x0 - 1.0 * sp <= x <= b.x1 + 1.0 * sp:
                    continue
                members = [c for c in chords if bi in c.beams]
                # the number sits at the beam, or (Finale) on the notehead side of the group
                head_side = members and min(abs(h.glyph.y - y) for c in members for h in c.heads) < 2.5 * sp \
                    and (y < min(c.top for c in members)) == (_beam_y(b, x) > max(c.bottom for c in members))
                if head_side and abs(_beam_y(b, x) - y) >= 3.5 * sp:
                    # only when the group splits evenly into n notes of a value it contains
                    vals = [_dur(c.value, c.dots, None) for c in members]
                    if sum(vals) / n not in vals:
                        continue
                if abs(_beam_y(b, x) - y) < 3.5 * sp or head_side:
                    group = members
                    if group:
                        # include chords sharing any beam with the group
                        ids = {i for c in group for i in c.beams}
                        group = [c for c in chords if set(c.beams) & ids]
                        break
            if not group:
                # bracket: horizontal segments next to the number
                hsegs = [  # brackets follow the notes, so they may slant
                    s for s in self.pc.segments
                    if abs(s.y1 - s.y0) < 0.35 * abs(s.x1 - s.x0)
                    and abs((s.y0 + s.y1) / 2 - y) < 1.5 * sp and abs(s.x1 - s.x0) > 0.8 * sp
                    and (abs(s.x1 - x) < 3 * sp or abs(s.x0 - x) < 3 * sp)
                ]
                lo = min((min(s.x0, s.x1) for s in hsegs), default=0.0)
                hi = max((max(s.x0, s.x1) for s in hsegs), default=0.0)
                hooks = [
                    s for s in self.pc.segments
                    if hsegs and abs(s.x1 - s.x0) < 0.2 * sp and 0.4 * sp < abs(s.y1 - s.y0) < 2 * sp
                    and min(abs(s.x0 - lo), abs(s.x0 - hi)) < 0.3 * sp
                    and abs((s.y0 + s.y1) / 2 - y) < 2 * sp
                ]
                if hsegs and not hooks and not any(s.is_horizontal for s in hsegs):
                    hsegs = []  # a slanted line is a bracket only with a hook at one of its ends
                if hooks:
                    # hooks point towards the notes: a bracket between two staves
                    # belongs to the staff its hooks point at
                    up = 0
                    for h in hooks:
                        ends = [(s.x0, s.y0) for s in hsegs] + [(s.x1, s.y1) for s in hsegs]
                        ey = min(ends, key=lambda e: abs(e[0] - h.x0))[1]
                        tip = max((h.y0, h.y1), key=lambda v: abs(v - ey))
                        up += tip < ey
                    points_up = up * 2 > len(hooks)
                    bracket_y = sum((s.y0 + s.y1) / 2 for s in hsegs) / len(hsegs)
                    if (points_up and st.top > bracket_y) or (not points_up and st.bottom < bracket_y):
                        continue
                if hsegs:
                    group = [c for c in chords if lo - 0.5 * sp <= c.x <= hi + 0.2 * sp]
                    # two voices sharing a beat: the bracket belongs to the voice on its stem side
                    side = "down" if y > st.middle else "up"
                    group = [
                        c for c in group
                        if c.direction in (side, None)
                        or not any(o is not c and abs(o.x - c.x) < 0.5 * sp and o.direction == side for o in group)
                    ]
                    group += [r for r in rests if lo - 0.5 * sp <= r.x <= hi + 0.2 * sp]
            if not group:
                near = sorted(
                    [c for c in chords if abs(c.x - x) < 4 * sp] + [r for r in rests if abs(r.x - x) < 4 * sp],
                    key=lambda e: abs(e.x - x),
                )
                group = near[: n]
            if group and not self._near_group(st, group, y):
                continue
            for e in group:
                if e.tuplet is None:
                    e.tuplet = (n, normal_n)
            # rests between the tuplet's chords belong to it as well
            if group:
                lo = min(e.x for e in group)
                hi = max(e.x for e in group)
                for r in rests:
                    if lo < r.x < hi and r.tuplet is None and abs(r.x - x) < 6 * sp:
                        r.tuplet = (n, normal_n)

    def _near_group(self, st: Staff, group, y: float) -> bool:
        """The tuplet number must sit next to its notes, not next to another staff's."""
        sp = st.space
        ys = []
        for e in group:
            if isinstance(e, Chord):
                ys += [h.glyph.y for h in e.heads]
                if e.tip is not None:
                    ys.append(e.tip)
            else:
                ys.append(e.glyph.y)
        if not ys:
            return False
        d = min(abs(v - y) for v in ys)
        if d <= 3.5 * sp:
            return True
        # a little further out beyond the stem tips, as long as no other staff is close
        tips = [c.tip for c in group if isinstance(c, Chord) and c.tip is not None]
        beyond = tips and (all(y > t for t in tips) if all(c.direction == "down" for c in group
                                                          if isinstance(c, Chord)) else all(y < t for t in tips))
        crowded = any(o is not st and o.distance(y) < 4 * sp for o in self.system.staves + self.other_staves)
        return d <= 4.5 * sp and bool(beyond) and not crowded

    def _multirests(self, st, glyphs, used_ids) -> list[tuple[float, int]]:
        sp = st.space
        out = []
        for p in self.pc.polygons:
            if p.x1 < st.x0 or p.x0 > st.x1:
                continue
            h = p.y1 - p.y0
            w = p.x1 - p.x0
            if w > 3 * sp and 0.5 * sp <= h <= 1.4 * sp and st.top + sp <= (p.y0 + p.y1) / 2 <= st.bottom - sp:
                digits = [
                    g for g in glyphs
                    if g.name.startswith("time.") and g.name[5:].isdigit()
                    and p.x0 - sp <= g.cx <= p.x1 + sp and st.top - 4 * sp <= g.y < st.top + 0.5 * sp
                ]
                if digits:
                    digits.sort(key=lambda g: g.x)
                    n = int("".join(g.name[5:] for g in digits))
                    used_ids |= {id(g) for g in digits}
                    out.append(((p.x0 + p.x1) / 2, n))
        for g in glyphs:
            if g.name == "rest.hbar":
                digits = [
                    d for d in glyphs
                    if d.name.startswith("time.") and d.name[5:].isdigit() and g.x0 - sp <= d.cx <= g.x1 + sp
                    and d.y < st.top + 0.5 * sp
                ]
                if digits:
                    digits.sort(key=lambda d: d.x)
                    used_ids |= {id(d) for d in digits}
                    out.append((g.cx, int("".join(d.name[5:] for d in digits))))
        return out

    def _accidentals(self, st, accs, chords) -> None:
        sp = st.space
        heads = [h for c in chords for h in c.heads]
        for a in accs:
            cands = [
                h for h in heads
                if abs(h.glyph.y - a.y) < 0.35 * sp and -0.2 * sp <= h.glyph.x - a.x1 <= 3.5 * sp
                and h.accidental is None
            ]
            if not cands:
                continue
            h = min(cands, key=lambda h: h.glyph.x - a.x1)
            h.accidental = ACC_ALTER[a.name]

    def _directions(self, st, glyphs, texts) -> list[Direction]:
        sp = st.space
        out = []
        dyn = sorted([g for g in glyphs if g.name.startswith("dyn.")], key=lambda g: g.x)
        for grp in _cluster_x(dyn, 0.4 * sp, by_gap=True):
            word = "".join(g.name[4:] for g in grp)
            if word in ("p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sf", "sfz", "fp", "sfp", "sfpp", "rf", "rfz", "fz"):
                out.append(Direction(x=grp[0].x, staff=1, kind="dynamic", text=word, above=grp[0].y < st.top))
        return out

    # ------------------------------------------------------------------ events
    def _build_events(self, st, chords, rests, clef, key, clef_changes, expected, measure) -> list[Event]:
        sp = st.space
        if self._is_multivoice(chords, sp):
            self._split_voices(st, chords, rests)
        events = self._make_events(chords, rests, clef, key, clef_changes, expected)
        if self._voice_errors(events, expected) and rests and self._second_voice_rests(st, chords, rests, expected):
            # a rest under or over a note, or rests on top of a measure the notes already fill,
            # belong to a second voice even when no opposite stems show it
            saved = [(it, it.voice) for it in chords + rests]
            self._split_voices(st, chords, rests)
            alt = self._make_events(chords, rests, clef, key, clef_changes, expected)
            if not self._voice_errors(alt, expected):
                events = alt
            else:
                for it, v in saved:
                    it.voice = v
        if self._voice_errors(events, expected):
            self._fill_hidden_rests(events, expected, sp)
        for v, total in self._voice_errors(events, expected):
            measure.warnings.append(f"voice {v}: {total} != {expected}")
        return events

    def _fill_hidden_rests(self, events, expected, sp) -> None:
        """Notation programs hide rests in secondary voices; put them back as invisible rests,
        timed by the notes of a complete voice at the same horizontal position."""
        voices = {}
        for e in events:
            if not e.grace:
                voices.setdefault(e.voice, []).append(e)
        complete = [v for v, ves in voices.items() if sum(e.duration for e in ves) == expected]
        if not complete:
            return
        grid: list[tuple[float, Fraction]] = []
        for v in complete:
            t = Fraction(0)
            for e in sorted(voices[v], key=lambda e: e.x):
                grid.append((e.x, t))
                t += e.duration
        for v, ves in voices.items():
            if v in complete or sum(e.duration for e in ves) > expected:
                continue
            ves.sort(key=lambda e: e.x)
            times = []
            for e in ves:
                hits = [t for x, t in grid if abs(x - e.x) < 1.0 * sp]
                if not hits:
                    break
                times.append(min(hits))
            if len(times) != len(ves):
                continue
            cur = Fraction(0)
            fill = []
            for e, t in zip(ves, times):
                if t < cur:
                    break
                if t > cur:
                    fill.append(_hidden_rest(e.x - 0.01, t - cur, v))
                cur = t + e.duration
            else:
                if cur > expected:
                    continue
                if cur < expected:
                    fill.append(_hidden_rest(ves[-1].x + 0.01, expected - cur, v))
                events.extend(fill)

    def _split_voices(self, st, chords, rests) -> None:
        sp = st.space
        both = {c.direction for c in chords if not c.grace} >= {"up", "down"}
        for c in chords:
            if not both:
                c.voice = 1
            elif c.direction is not None:
                c.voice = 1 if c.direction == "up" else 2
            else:
                c.voice = 1 if c.top < st.middle else 2
        for r in rests:
            ry = r.glyph.y
            near = [c for c in chords if abs(c.x - r.x) < 1.2 * sp and not c.grace]
            if near:
                # a rest sharing a beat with a note goes to the other voice
                r.voice = 2 if ry > max(c.bottom for c in near) else 1
                if r.voice == min(c.voice for c in near) and len({c.voice for c in chords}) > 1:
                    r.voice = 3 - r.voice
            elif not {c.voice for c in chords} >= {2} and chords:
                r.voice = 2
            else:
                r.voice = 1 if ry < st.middle - 0.3 * sp else 2 if ry > st.middle + 0.3 * sp else 1

    @staticmethod
    def _second_voice_rests(st, chords, rests, expected) -> bool:
        sp = st.space
        if any(abs(c.x - r.x) < 1.2 * sp for c in chords if not c.grace for r in rests):
            return True
        notes = sum(_dur(c.value, c.dots, c.tuplet) for c in chords if not c.grace)
        return notes == expected

    @staticmethod
    def _voice_errors(events, expected):
        out = []
        for v in sorted({e.voice for e in events}):
            total = sum(e.duration for e in events if e.voice == v)
            if total != expected:
                out.append((v, total))
        return out

    def _make_events(self, chords, rests, clef, key, clef_changes, expected) -> list[Event]:
        events: list[Event] = []
        altered: dict[int, int] = {}  # diatonic -> alter within the measure
        items = sorted(chords + rests, key=lambda e: e.x)
        cur_clef = clef
        for it in items:
            for x, _, c in clef_changes:
                if x <= it.x:
                    cur_clef = c
            if isinstance(it, Chord):
                ref = _clef_reference(cur_clef)
                pitches = []
                for h in sorted(it.heads, key=lambda h: -h.pos):
                    dia = ref + h.pos
                    octave, si = divmod(dia, 7)
                    step = STEPS[si]
                    if h.accidental is not None:
                        alter = h.accidental
                        altered[dia] = alter
                    elif dia in altered:
                        alter = altered[dia]
                    else:
                        alter = _key_alter(key, step)
                    pitches.append(Pitch(step=step, octave=octave, alter=alter,
                                         tie_start=h.tie_start, tie_stop=h.tie_stop))
                    if h.tie_stop and h.accidental is None:
                        pitches[-1]._keep_from_tie = True  # type: ignore[attr-defined]
                ev = Event(
                    x=it.x, duration=_dur(it.value, it.dots, it.tuplet), pitches=pitches, voice=it.voice,
                    base=it.value, dots=it.dots, tuplet=it.tuplet, grace=it.grace, stem=it.direction,
                    articulations=list(it.articulations),
                    notehead="slash" if any(h.glyph.name.startswith("head.slash") for h in it.heads) else None,
                )
                if it.grace:
                    ev.duration = Fraction(0)
                events.append(ev)
            else:
                ev = Event(
                    x=it.x, duration=_dur(it.value, it.dots, it.tuplet), voice=it.voice,
                    base=it.value, dots=it.dots, tuplet=it.tuplet,
                )
                events.append(ev)
        # whole rests fill the measure; so do measure-centered rests alone in their voice
        for v in sorted({e.voice for e in events}):
            ves = [e for e in events if e.voice == v and not e.grace]
            if len(ves) == 1 and ves[0].is_rest and ves[0].base in (Fraction(4), Fraction(8)):
                ves[0].duration = expected
                ves[0].full_measure = True
        return events

    def _is_multivoice(self, chords: list[Chord], sp: float) -> bool:
        ups = [c for c in chords if c.direction == "up" and not c.grace]
        downs = [c for c in chords if c.direction == "down" and not c.grace]
        for u in ups:
            for d in downs:
                if abs(u.x - d.x) < 1.6 * sp and u.stem is not d.stem:
                    # stems facing away from each other at the same moment
                    if u.bottom <= d.top + 0.6 * sp:
                        return True
        return False


# ---------------------------------------------------------------------- helpers
def _median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


def _hidden_rest(x: float, duration: Fraction, voice: int) -> Event:
    base, dots = duration, 0
    undotted = duration * 2 / 3
    if undotted in HEAD_VALUE.values() or undotted in REST_VALUE.values():
        base, dots = undotted, 1  # dotted value
    return Event(x=x, duration=duration, voice=voice, base=base, dots=dots, hidden=True)


def _dur(value: Fraction, dots: int, tuplet) -> Fraction:
    d = value
    add = value
    for _ in range(dots):
        add /= 2
        d += add
    if tuplet:
        d = d * Fraction(tuplet[1], tuplet[0])
    return d


def _fifths(accs: list[Glyph]) -> int:
    sharps = sum(1 for a in accs if a.name == "acc.sharp")
    flats = sum(1 for a in accs if a.name == "acc.flat")
    return sharps - flats


def _parse_time(digits: list[Glyph], st: Staff):
    sym = [g for g in digits if g.name in ("time.common", "time.cut")]
    if sym:
        return ((4, 4), "common") if sym[0].name == "time.common" else ((2, 2), "cut")
    nums = [g for g in digits if g.name[5:].isdigit()]
    top = sorted([g for g in nums if g.y < st.middle], key=lambda g: g.x)
    bot = sorted([g for g in nums if g.y >= st.middle], key=lambda g: g.x)
    if not top or not bot:
        return None, None
    try:
        return (int("".join(g.name[5:] for g in top)), int("".join(g.name[5:] for g in bot))), None
    except ValueError:
        return None, None


def _cluster_x(glyphs: list[Glyph], gap: float, by_gap: bool = False) -> list[list[Glyph]]:
    out: list[list[Glyph]] = []
    for g in sorted(glyphs, key=lambda g: g.x):
        if out:
            last = out[-1][-1]
            dist = (g.x0 - last.x1) if by_gap else (g.x - last.x)
            if dist < gap:
                out[-1].append(g)
                continue
        out.append([g])
    return out


def _thickness(p: Polygon) -> float:
    pts = p.points
    if len(pts) < 4:
        return p.y1 - p.y0
    xs = sorted(pts, key=lambda q: q[0])
    left = [q for q in pts if abs(q[0] - xs[0][0]) < 0.5]
    right = [q for q in pts if abs(q[0] - xs[-1][0]) < 0.5]
    tl = max(q[1] for q in left) - min(q[1] for q in left)
    tr = max(q[1] for q in right) - min(q[1] for q in right)
    return (tl + tr) / 2


def _beam_y(p: Polygon, x: float) -> float:
    """Centre line of a beam polygon at x."""
    pts = p.points or [(p.x0, p.y0), (p.x1, p.y1)]
    xs = sorted(pts, key=lambda q: q[0])
    lx, rx = xs[0][0], xs[-1][0]
    left = [q[1] for q in pts if abs(q[0] - lx) < 0.5]
    right = [q[1] for q in pts if abs(q[0] - rx) < 0.5]
    ly = sum(left) / len(left)
    ry = sum(right) / len(right)
    if rx - lx < 1e-6:
        return ly
    t = (x - lx) / (rx - lx)
    return ly + t * (ry - ly)


def _label(texts: list[Text], st: Staff) -> str:
    cands = [
        t for t in texts
        if t.x1 <= st.x0 + 0.5 * st.space and st.top - 2 * st.space <= t.origin_y <= st.bottom + 2 * st.space
    ]
    cands.sort(key=lambda t: (t.origin_y, t.x0))
    return " ".join(t.text for t in cands).strip()
