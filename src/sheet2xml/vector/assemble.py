"""Read a whole vector PDF: pages -> systems -> parts with continuous measures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from ..model import Event, Measure, Part, Score
from ..transpose import transposition_for
from .extract import PageContent, extract
from .layout import System, find_staves, find_systems
from .recognize import StaffContent, SystemReader


@dataclass
class SystemRead:
    page: PageContent
    system: System
    groups: list[list[StaffContent]]  # staves grouped into instruments
    labels: list[str]


def _norm(s: str) -> str:
    s = s.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return re.sub(r"[^a-z0-9 ]", " ", s)


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _label_score(label: str, name: str) -> float:
    """How well an (abbreviated) staff label matches a full instrument name."""
    a, b = _norm(label).split(), _norm(name).split()
    if not a or not b:
        return 0.0
    if a == b:
        return 3.0
    num_a = [_ROMAN[t] for t in a if t in _ROMAN]
    num_b = [_ROMAN[t] for t in b if t in _ROMAN]
    words_a = [t for t in a if t not in _ROMAN]
    words_b = [t for t in b if t not in _ROMAN]
    if num_a != num_b:
        return 0.1 if words_a and words_b and words_a[0][:1] == words_b[0][:1] else 0.0
    if not words_a or not words_b:
        return 1.0
    wa, wb = words_a[0], " ".join(words_b)
    if wa[0] != wb[0]:
        return 0.0
    # abbreviation letters must appear in order in the full name
    i = 0
    for ch in wa:
        i = wb.find(ch, i)
        if i < 0:
            return 0.2
        i += 1
    return 2.0


def _label_blocks(page: PageContent, system: System) -> list[tuple[float, str]]:
    """Instrument names left of the system, merged over line breaks: (y, text)."""
    sp = system.staves[0].space
    texts = [
        t for t in page.texts
        if t.x1 <= system.x0 + 0.5 * sp and system.top - 3 * sp <= t.origin_y <= system.bottom + 3 * sp
        and not t.text.isdigit()
    ]
    texts.sort(key=lambda t: (t.origin_y, t.x0))
    blocks: list[list] = []
    for t in texts:
        if blocks:
            last = blocks[-1]
            lt = last[-1]
            same_line = abs(t.origin_y - lt.origin_y) < 0.5 * t.size
            next_line = 0 < t.origin_y - lt.origin_y < 1.5 * max(t.size, lt.size) and abs(t.cx - lt.cx) < 6 * sp
            if same_line or next_line:
                last.append(t)
                continue
        blocks.append([t])
    out = []
    for b in blocks:
        y = (min(t.y0 for t in b) + max(t.y1 for t in b)) / 2
        out.append((y, " ".join(t.text for t in b).strip()))
    return out


def _group_staves(contents: list[StaffContent], system: System, page: PageContent):
    """Group staves into instruments and name each group. Returns (groups, labels)."""
    n = len(contents)
    own: list[list[str]] = [[] for _ in range(n)]
    between: dict[int, list[str]] = {}
    for y, text in _label_blocks(page, system):
        placed = False
        for i, c in enumerate(contents):
            st = c.staff
            if st.top - 1.0 * st.space <= y <= st.bottom + 1.0 * st.space:
                own[i].append(text)
                placed = True
                break
        if placed:
            continue
        for i in range(n - 1):
            if contents[i].staff.bottom < y < contents[i + 1].staff.top:
                between.setdefault(i, []).append(text)
                break
    groups: list[list[StaffContent]] = []
    labels: list[str] = []
    braces = sorted(system.braces)
    i = 0
    while i < n:
        br = next((b for b in braces if b[0] == i and b[1] - b[0] <= 3), None)
        if br:
            idx = list(range(br[0], br[1] + 1))
            names = [t for k in idx for t in own[k]] + [t for k in idx[:-1] for t in between.get(k, [])]
            if len(names) <= 1:
                groups.append([contents[k] for k in idx])
                labels.append(names[0] if names else "")
                i = br[1] + 1
                continue
        if not own[i] and i + 1 < n and between.get(i) and not own[i + 1]:
            groups.append([contents[i], contents[i + 1]])
            labels.append(between[i][0])
            i += 2
            continue
        groups.append([contents[i]])
        labels.append(own[i][0] if own[i] else "")
        i += 1
    for g, lb in zip(groups, labels):
        for c in g:
            c.label = lb
    return groups, labels


def read_vector_pdf(path: str) -> Score:
    pages = extract(path)
    score = Score(source="vector")
    reads: list[SystemRead] = []
    carry_by_part: dict[int, dict] = {}
    for pc in pages:
        staves = find_staves(pc)
        if not staves:
            continue
        for sy in find_systems(pc, staves):
            # carry per staff index inside the system; refined below per part
            reader = SystemReader(pc, sy, carry={}, page_staves=staves)
            reads.append(SystemRead(page=pc, system=sy, groups=[], labels=[]))
            reads[-1]._reader = reader  # type: ignore[attr-defined]
    if not reads:
        raise ValueError("Keine Notensysteme im PDF gefunden")

    _fill_metadata(score, pages, reads)

    # instrument groups of every system; the system showing the most groups
    # (usually the first, but not when the opening omits e.g. choir and piano)
    # defines the parts, the others are aligned to it
    layouts = []
    for sr in reads:
        reader: SystemReader = sr._reader  # type: ignore[attr-defined]
        contents_pre = reader._assign()
        layouts.append(_group_staves(contents_pre, sr.system, sr.page))
    ref = max(range(len(reads)), key=lambda k: (len(layouts[k][0]), -k))
    parts: list[Part] = [
        Part(name=lb or f"Stimme {i + 1}", staves=len(g)) for i, (g, lb) in enumerate(zip(*layouts[ref]))
    ]
    mappings: list[list[int]] = [[] for _ in reads]
    mappings[ref] = list(range(len(parts)))
    # align the other systems, fullest first; instruments that the reference
    # system does not show become new parts at their place in the order
    for k in sorted(range(len(reads)), key=lambda k: (-len(layouts[k][0]), k)):
        if k == ref:
            continue
        groups, labels = layouts[k]
        mapping = _map_groups(parts, groups, labels, False)
        for gi, grp in enumerate(groups):
            if mapping[gi] is not None:
                continue
            lo = max([x for x in mapping[:gi] if x is not None], default=-1)
            hi = min([x for x in mapping[gi + 1:] if x is not None], default=len(parts))
            free = [j for j in range(lo + 1, hi) if j not in mapping and parts[j].staves == len(grp)]
            fits = [j for j in free if not labels[gi] or _label_score(labels[gi], parts[j].name) > 0.3]
            if fits:
                mapping[gi] = fits[0]
                continue
            pos = lo + 1
            parts.insert(pos, Part(name=labels[gi] or f"Stimme {pos + 1}", staves=len(grp)))
            for other in mappings:
                other[:] = [x + 1 if x >= pos else x for x in other]
            mapping = [x + 1 if x is not None and x >= pos else x for x in mapping]
            mapping[gi] = pos
        mappings[k] = mapping  # type: ignore[assignment]

    part_carry: list[dict] = [{} for _ in parts]
    seen_labels: list[list[str]] = [[] for _ in parts]
    for k, sr in enumerate(reads):
        reader = sr._reader  # type: ignore[attr-defined]
        groups, labels = layouts[k]
        mapping = mappings[k]
        for gi, grp in enumerate(groups):
            pi = mapping[gi]
            if labels[gi]:
                seen_labels[pi].append(labels[gi])
            part = parts[pi]
            part.staves = max(part.staves, len(grp))
            for si, sc in enumerate(grp):
                carry = part_carry[pi].setdefault(si, {})
                reader.carry = {0: carry}
                reader._read_staff(0, sc, sr.system.barlines)
        sr.groups = groups
        sr.labels = labels
        sr._mapping = mapping  # type: ignore[attr-defined]

    # full name from the first labelled system, the shortest other label as abbreviation
    for part, seen in zip(parts, seen_labels):
        if not seen:
            continue
        part.name = seen[0]
        others = [lb for lb in seen if _norm(lb) != _norm(part.name)]
        if others:
            part.abbreviation = min(others, key=len)

    _single_part_name(score, parts, pages, reads)
    for p in parts:
        p.transpose = transposition_for(p.name)
    _stitch(score, parts, reads)
    _accept_partial_measures(parts)
    score.parts = parts
    return score


def _voice_totals(m: Measure) -> set[Fraction]:
    return {sum((e.duration for e in m.events if e.voice == v), Fraction(0)) for v in {e.voice for e in m.events}}


def _accept_partial_measures(parts: list[Part]) -> None:
    """Short measures that are normal notation, not reading errors: a pickup at the
    start of the piece or of a movement, the short last measure, and a measure split
    in two by a repeat or double barline. They count only if every part agrees."""
    if not parts or not parts[0].measures:
        return
    n = min(len(p.measures) for p in parts)

    def short(i: int) -> Fraction | None:
        length = None
        for p in parts:
            m = p.measures[i]
            if any(e.full_measure for e in m.events) or not m.events:
                continue
            totals = _voice_totals(m)
            if len(totals) != 1:
                return None
            t = next(iter(totals))
            if not 0 < t < (m.expected or 0) or (length is not None and t != length):
                return None
            length = t
        return length

    lengths = [short(i) for i in range(n)]
    ok = set()
    for i, length in enumerate(lengths):
        if length is None:
            continue
        expected = parts[0].measures[i].expected
        bar = parts[0].measures[i].barline
        prev_bar = parts[0].measures[i - 1].barline if i else "final"
        if i == n - 1 or bar in ("final", "repeat-end") or prev_bar in ("final", "repeat-end"):
            ok.add(i)
        elif i + 1 < n and lengths[i + 1] is not None and length + lengths[i + 1] == expected:
            ok |= {i, i + 1}
            for p in parts:
                p.measures[i + 1].implicit = i > 0
    for p in parts:
        for i in ok:
            m = p.measures[i]
            m.partial = True
            m.warnings = [w for w in m.warnings if not w.startswith("voice")]


def _map_groups(parts: list[Part], groups, labels, first: bool) -> list[int | None]:
    """Map the instrument groups of a system onto the known parts.

    Only the first system creates parts (it normally shows every instrument);
    later systems are aligned to them in order, using the labels."""
    n = len(groups)
    if not parts:
        return [None] * n
    m = len(parts)

    def sim(i: int, j: int) -> float:
        lb = labels[i]
        p = parts[j]
        s = (
            max(_label_score(lb, p.name), _label_score(p.name, lb), _label_score(lb, p.abbreviation or ""))
            if lb
            else 0.6
        )
        if len(groups[i]) != p.staves:
            s -= 1.0
        return s

    if n == m and all(sim(i, i) > 0.3 for i in range(n)):
        return list(range(n))
    NEG = -1e9
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    back: list[list] = [[None] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[0][j] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if dp[i][j - 1] > dp[i][j]:
                dp[i][j] = dp[i][j - 1]
                back[i][j] = ("skip", None)
            v = dp[i - 1][j - 1] + sim(i - 1, j - 1)
            if v > dp[i][j]:
                dp[i][j] = v
                back[i][j] = ("take", j - 1)
    out: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        step = back[i][j]
        if step is None:
            break
        if step[0] == "take":
            out[i - 1] = step[1]
            i, j = i - 1, j - 1
        else:
            j -= 1
    return out


def _stitch(score: Score, parts: list[Part], reads: list[SystemRead]) -> None:
    """Concatenate measures system by system; fill parts missing from a system."""
    number = 1
    last_time: tuple[int, int] = (4, 4)
    for sr in reads:
        mapping = sr._mapping  # type: ignore[attr-defined]
        # number of measures in this system = max over staves
        counts = [len(sc.measures) for g in sr.groups for sc in g]
        if not counts:
            continue
        n = max(counts)
        expansions: list[int] = [1] * n
        for g in sr.groups:
            for sc in g:
                for mi, m in enumerate(sc.measures):
                    expansions[mi] = max(expansions[mi], m.rest_measures)
        present = set()
        for gi, g in enumerate(sr.groups):
            pi = mapping[gi]
            if pi is None or pi < 0:
                continue
            present.add(pi)
            part = parts[pi]
            for mi in range(n):
                merged = Measure(number=0)
                ref = None
                for si, sc in enumerate(g):
                    if mi < len(sc.measures):
                        m = sc.measures[mi]
                    else:
                        m = Measure(number=0)
                        m.warnings.append("missing measure")
                    ref = ref or m
                    _merge_staff(merged, m, si + 1)
                _expand(part, merged, expansions[mi])
                last_time = getattr(ref, "_time", last_time) if ref is not None else last_time
        for pi, part in enumerate(parts):
            if pi in present:
                continue
            # part hidden in this system (empty staves hidden): add full-measure rests
            for mi in range(n):
                tmpl = _template_measure(sr, mi)
                rest_len = tmpl[0]
                m = Measure(number=0)
                for s in range(1, part.staves + 1):
                    m.events.append(Event(x=0, duration=rest_len, staff=s, base=Fraction(4), full_measure=True))
                m.expected = rest_len
                m._time = tmpl[1]  # type: ignore[attr-defined]
                _expand(part, m, expansions[mi])
    # number measures, compute time/key changes per part
    for part in parts:
        prev_time = None
        prev_key = None
        for i, m in enumerate(part.measures, start=1):
            m.number = i
            t = getattr(m, "_time", None)
            k = getattr(m, "_key", None)
            if t and t != prev_time:
                m.time = t
                m.time_symbol = getattr(m, "_time_symbol", None)
                prev_time = t
            elif m.time == prev_time:
                m.time = None
            if k is not None and k != prev_key:
                m.key = k
                prev_key = k
            else:
                m.key = None if i > 1 else m.key
    lengths = {len(p.measures) for p in parts}
    if len(lengths) > 1:
        score.warnings.append(f"Stimmen haben unterschiedlich viele Takte: {sorted(lengths)}")
        longest = max(lengths)
        for p in parts:
            while len(p.measures) < longest:
                last = p.measures[-1] if p.measures else None
                exp = last.expected if last else Fraction(4)
                m = Measure(number=len(p.measures) + 1, expected=exp)
                m.events.append(Event(x=0, duration=exp, base=Fraction(4), full_measure=True))
                p.measures.append(m)


def _template_measure(sr: SystemRead, mi: int):
    for g in sr.groups:
        for sc in g:
            if mi < len(sc.measures):
                m = sc.measures[mi]
                return m.expected or Fraction(4), getattr(m, "_time", (4, 4))
    return Fraction(4), (4, 4)


def _merge_staff(dst: Measure, src: Measure, staff: int) -> None:
    if hasattr(src, "_geom"):
        geoms = getattr(dst, "_geoms", [])
        geoms.append((staff,) + src._geom)  # type: ignore[attr-defined]
        dst._geoms = geoms  # type: ignore[attr-defined]
    for e in src.events:
        e.staff = staff
        if staff > 1:
            e.voice += 4 * (staff - 1)
        dst.events.append(e)
    for x, _, c in src.clef_changes:
        dst.clef_changes.append((x, staff, c))
    for c in src.clefs.values():
        dst.clefs[staff] = c
    for d in src.directions:
        d.staff = staff
        dst.directions.append(d)
    if staff == 1:
        dst.time = src.time
        dst.time_symbol = src.time_symbol
        dst.key = src.key
        dst.expected = src.expected
        dst.barline = src.barline
        dst.repeat_start = src.repeat_start
        dst.rest_measures = src.rest_measures
        for attr in ("_time", "_time_symbol", "_key"):
            if hasattr(src, attr):
                setattr(dst, attr, getattr(src, attr))
    dst.warnings.extend(src.warnings)


def _expand(part: Part, m: Measure, count: int) -> None:
    if count <= 1:
        part.measures.append(m)
        return
    # a multi-measure rest becomes `count` empty measures
    for k in range(count):
        mm = Measure(number=0, expected=m.expected)
        for attr in ("_time", "_time_symbol", "_key"):
            if hasattr(m, attr):
                setattr(mm, attr, getattr(m, attr))
        if k == 0:
            mm.clefs = m.clefs
            mm.time, mm.key, mm.time_symbol = m.time, m.key, m.time_symbol
            mm.directions = m.directions
        if k == 0:
            mm.repeat_start = m.repeat_start
        if k == count - 1:
            mm.barline = m.barline
        for s in range(1, part.staves + 1):
            mm.events.append(Event(x=0, duration=m.expected or Fraction(4), staff=s, base=Fraction(4),
                                   full_measure=True, voice=1 + 4 * (s - 1)))
        part.measures.append(mm)


def _fill_metadata(score: Score, pages: list[PageContent], reads: list[SystemRead]) -> None:
    first = pages[0]
    first_staff_top = None
    for sr in reads:
        if sr.page is first or first_staff_top is None:
            first_staff_top = sr.system.top if first_staff_top is None else first_staff_top
            break
    page = reads[0].page if reads else first
    limit = reads[0].system.top if reads else page.height / 3
    head = [t for t in page.texts if t.origin_y < limit and len(t.text) > 1]
    if not head:
        return
    biggest = max(head, key=lambda t: t.size)
    same_line = [t for t in head if abs(t.origin_y - biggest.origin_y) < 2 and t.size >= biggest.size * 0.9]
    score.title = " ".join(t.text for t in sorted(same_line, key=lambda t: t.x0))
    sp = reads[0].system.staves[0].space if reads else 2.0
    # tempo marks and expressions sit just above the first staff
    rest = [t for t in head if t not in same_line and t.origin_y < limit - 5 * sp and "=" not in t.text]
    subs = [
        t for t in rest
        if t.size >= 0.6 * biggest.size and abs(t.cx - page.width / 2) < page.width * 0.2
        and t.origin_y < biggest.origin_y + 4 * biggest.size
    ]
    if subs:
        score.subtitle = " ".join(t.text for t in sorted(subs, key=lambda t: t.origin_y))
    credits = [t for t in rest if t not in subs and (t.x1 > page.width * 0.65 or t.x0 < page.width * 0.3)]
    composer: list[str] = []
    lyricist: list[str] = []
    arranger: list[str] = []
    last: list[str] | None = None
    for t in sorted(credits, key=lambda t: t.origin_y):
        low = t.text.lower()
        if last is not None and last[-1].rstrip().endswith((",", "&", "und")):
            target = last  # continuation of the previous line
        elif re.match(r"(text|worte|dichtung|lyrics|words|deutsch|übers|übertr)", low):
            target = lyricist
        elif re.match(r"(arr|aran|bearb|satz|orch|setting)", low):
            target = arranger
        elif t.x1 > page.width * 0.65:
            target = composer
        else:
            continue
        target.append(t.text)
        last = target
    score.composer = "\n".join(composer)
    score.lyricist = "\n".join(lyricist)
    score.arranger = "\n".join(arranger)


def _single_part_name(score: Score, parts: list[Part], pages, reads) -> None:
    if len(parts) != 1 or not parts[0].name.startswith("Stimme"):
        return
    page = reads[0].page
    top = reads[0].system.top
    cands = [
        t for t in page.texts
        if t.origin_y < top and t.x0 < page.width * 0.35 and len(t.text) > 2 and t.text not in score.title
    ]
    if cands:
        parts[0].name = min(cands, key=lambda t: t.origin_y).text
