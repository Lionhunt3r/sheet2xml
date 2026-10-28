"""Find staves, systems and barlines among the vector shapes of a page."""

from __future__ import annotations

from dataclasses import dataclass, field

from .extract import Curve, Glyph, PageContent, Polygon, Segment


@dataclass
class Staff:
    lines: list[float]  # y of the five lines, top to bottom
    x0: float
    x1: float
    index: int = 0  # index on the page

    @property
    def top(self) -> float:
        return self.lines[0]

    @property
    def bottom(self) -> float:
        return self.lines[-1]

    @property
    def space(self) -> float:
        return (self.bottom - self.top) / 4

    @property
    def middle(self) -> float:
        return (self.top + self.bottom) / 2

    def distance(self, y: float) -> float:
        if y < self.top:
            return self.top - y
        if y > self.bottom:
            return y - self.bottom
        return 0.0


@dataclass
class System:
    staves: list[Staff]
    barlines: list[tuple[float, float, str]] = field(default_factory=list)  # (x0, x1, kind)
    braces: list[tuple[int, int]] = field(default_factory=list)  # staff index ranges joined by a brace

    @property
    def x0(self) -> float:
        return min(s.x0 for s in self.staves)

    @property
    def x1(self) -> float:
        return max(s.x1 for s in self.staves)

    @property
    def top(self) -> float:
        return self.staves[0].top

    @property
    def bottom(self) -> float:
        return self.staves[-1].bottom


def _merge_horizontal(segs: list[Segment]) -> list[tuple[float, float, float]]:
    """Merge collinear horizontal pieces into (y, x0, x1) lines."""
    items = sorted(
        ((round((s.y0 + s.y1) / 2, 1), min(s.x0, s.x1), max(s.x0, s.x1)) for s in segs),
        key=lambda t: (t[0], t[1]),
    )
    lines: list[list[float]] = []
    for y, a, b in items:
        merged = False
        for ln in reversed(lines[-8:]):
            if abs(ln[0] - y) <= 0.35 and a <= ln[2] + 3.0 and b >= ln[1] - 3.0:
                ln[1] = min(ln[1], a)
                ln[2] = max(ln[2], b)
                merged = True
                break
        if not merged:
            lines.append([y, a, b])
    return [tuple(ln) for ln in lines]


def find_staves(pc: PageContent) -> list[Staff]:
    horiz = [s for s in pc.segments if s.is_horizontal and abs(s.x1 - s.x0) > 25]
    lines = _merge_horizontal(horiz)
    lines = [ln for ln in lines if ln[2] - ln[1] > 40]
    lines.sort(key=lambda t: t[0])
    used = [False] * len(lines)
    staves: list[Staff] = []

    def overlaps(a, b) -> bool:
        # the five lines of a staff start and end together
        tol = max(3.0, 0.08 * (a[2] - a[1]))
        return abs(a[1] - b[1]) <= tol and abs(a[2] - b[2]) <= tol

    for i, top in enumerate(lines):
        if used[i]:
            continue
        found = None
        for j in range(i + 1, len(lines)):
            cand = lines[j]
            d = cand[0] - top[0]
            if d < 2.0:
                continue
            if d > 16:
                break
            if used[j] or not overlaps(top, cand):
                continue
            chain = [i, j]
            ok = True
            for k in range(2, 5):
                target = top[0] + k * d
                best = None
                for m in range(j + 1, len(lines)):
                    if lines[m][0] > target + 0.2 * d:
                        break
                    if not used[m] and abs(lines[m][0] - target) <= 0.12 * d and overlaps(top, lines[m]):
                        best = m
                        break
                if best is None:
                    ok = False
                    break
                chain.append(best)
            if ok:
                found = chain
                break
        if found:
            for m in found:
                used[m] = True
            ls = [lines[m] for m in found]
            staves.append(
                Staff(
                    lines=[ln[0] for ln in ls],
                    x0=max(ln[1] for ln in ls),
                    x1=min(ln[2] for ln in ls),
                )
            )
    staves.sort(key=lambda s: (s.top, s.x0))
    for i, s in enumerate(staves):
        s.index = i
    return staves


def _is_stem(seg: Segment, heads: list[Glyph], sp: float) -> bool:
    x = (seg.x0 + seg.x1) / 2
    y0, y1 = min(seg.y0, seg.y1), max(seg.y0, seg.y1)
    for h in heads:
        if h.x - 0.4 * sp <= x <= h.x + 1.8 * sp and (abs(h.y - y0) < 0.8 * sp or abs(h.y - y1) < 0.8 * sp):
            return True
    return False


def find_systems(pc: PageContent, staves: list[Staff]) -> list[System]:
    if not staves:
        return []
    heads = [g for g in pc.glyphs if g.name.startswith("head.")]
    parent = list(range(len(staves)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    verticals = [s for s in pc.segments if s.is_vertical and abs(s.y1 - s.y0) > 8]
    sp_med = sorted(s.space for s in staves)[len(staves) // 2]
    # shapes that join staves: vertical lines covering two or more staves, braces, brackets
    spans: list[tuple[float, float, float]] = []
    for v in verticals:
        spans.append(((v.x0 + v.x1) / 2, min(v.y0, v.y1), max(v.y0, v.y1)))
    for p in pc.polygons:
        if p.y1 - p.y0 > 6 * sp_med and p.x1 - p.x0 < 3 * sp_med:
            spans.append(((p.x0 + p.x1) / 2, p.y0, p.y1))
    for c in pc.curves:
        if c.y1 - c.y0 > 6 * sp_med and c.x1 - c.x0 < 3 * sp_med:
            spans.append(((c.x0 + c.x1) / 2, c.y0, c.y1))
    brace_spans = []
    for g in pc.glyphs:
        if g.name == "brace" and g.y1 - g.y0 > 4 * sp_med:
            spans.append((g.cx, g.y0, g.y1))
            brace_spans.append((g.cx, g.y0, g.y1))
    for c in pc.curves:
        if c.y1 - c.y0 > 6 * sp_med and c.x1 - c.x0 < 3 * sp_med:
            brace_spans.append(((c.x0 + c.x1) / 2, c.y0, c.y1))
    for p in pc.polygons:
        if p.y1 - p.y0 > 6 * sp_med and p.x1 - p.x0 < 3 * sp_med and len(p.points) > 8:
            brace_spans.append(((p.x0 + p.x1) / 2, p.y0, p.y1))

    for x, y0, y1 in spans:
        covered = [
            s.index for s in staves
            if s.x0 - 4 * s.space <= x <= s.x1 + s.space and y0 <= s.top + 0.6 * s.space and y1 >= s.bottom - 0.6 * s.space
        ]
        if len(covered) >= 2:
            for a in covered[1:]:
                union(covered[0], a)
    groups: dict[int, list[Staff]] = {}
    for s in staves:
        groups.setdefault(find(s.index), []).append(s)
    systems = [System(staves=sorted(g, key=lambda s: s.top)) for g in groups.values()]
    systems.sort(key=lambda sy: (sy.top, sy.x0))

    for sy in systems:
        sy.barlines = _system_barlines(sy, verticals, pc.polygons, heads)
    systems = _merge_aligned(systems, pc)
    for sy in systems:
        for x, y0, y1 in brace_spans:
            if x > sy.x0 + sp_med:
                continue
            idx = [i for i, s in enumerate(sy.staves) if y0 <= s.top + s.space and y1 >= s.bottom - s.space]
            if len(idx) >= 2:
                sy.braces.append((idx[0], idx[-1]))
    return systems


def _same_barlines(a: System, b: System, tol: float) -> bool:
    xa = [x for x, _, _ in a.barlines if x > a.x0 + tol]
    xb = [x for x, _, _ in b.barlines if x > b.x0 + tol]
    if len(xa) < 2 or len(xb) < 2:
        return False
    common = sum(1 for p in xa if any(abs(p - q) <= tol for q in xb))
    return common >= max(2, 0.6 * max(len(xa), len(xb)))


def _starts_system(sy: System, pc: PageContent | None) -> bool:
    """A measure number above the first staff at the left edge marks a new system."""
    if pc is None:
        return False
    st = sy.staves[0]
    sp = st.space
    for t in pc.texts:
        if t.text.strip().isdigit() and st.top - 5 * sp < t.y1 <= st.top + 0.5 * sp and abs(t.x0 - sy.x0) < 3 * sp:
            return True
    return False


def _merge_aligned(systems: list[System], pc: PageContent | None = None) -> list[System]:
    """Join staff groups whose barlines line up exactly: some programs (MuseScore)
    break barlines between instrument groups, so one system shows up as several."""
    if len(systems) < 2:
        return systems
    intra = [
        b.top - a.bottom
        for sy in systems
        for a, b in zip(sy.staves, sy.staves[1:])
    ]
    typical = sorted(intra)[len(intra) // 2] if intra else None
    out = [systems[0]]
    for sy in systems[1:]:
        prev = out[-1]
        tol = 0.5 * prev.staves[0].space
        gap = sy.top - prev.bottom
        same_frame = abs(prev.x0 - sy.x0) < tol and abs(prev.x1 - sy.x1) < tol
        multi = len(prev.staves) > 1 or len(sy.staves) > 1
        ends = [b[0] for b in prev.barlines][-1:] + [b[0] for b in sy.barlines][-1:]
        same_end = len(ends) == 2 and abs(ends[0] - ends[1]) < tol
        close = typical is not None and gap <= 3.0 * typical
        aligned = same_frame and (
            (_same_barlines(prev, sy, tol) and (typical is None or gap <= 2.0 * typical))
            or (same_end and close)
        )
        if aligned and multi and not _starts_system(sy, pc):
            prev.staves.extend(sy.staves)
            merged: list[tuple[float, float, str]] = []
            for b in sorted(prev.barlines + sy.barlines):
                if merged and b[0] - merged[-1][1] < 1.2 * prev.staves[0].space:
                    a = merged[-1]
                    kind = a[2] if a[2] != "single" else b[2]
                    merged[-1] = (a[0], max(a[1], b[1]), kind)
                else:
                    merged.append(b)
            prev.barlines = merged
            continue
        out.append(sy)
    return out


def _system_barlines(sy: System, verticals: list[Segment], polygons: list[Polygon], heads: list[Glyph]):
    sp = sum(s.space for s in sy.staves) / len(sy.staves)
    xs: list[tuple[float, float]] = []  # (x, thickness)
    cands: list[tuple[float, float, float, float]] = [
        ((v.x0 + v.x1) / 2, min(v.y0, v.y1), max(v.y0, v.y1), v.width) for v in verticals
    ]
    for p in polygons:  # thick final barlines drawn as filled rectangles
        if p.x1 - p.x0 < 1.0 * sp and p.y1 - p.y0 > 3.5 * sp:
            cands.append(((p.x0 + p.x1) / 2, p.y0, p.y1, p.x1 - p.x0))
    for x, y0, y1, w in cands:
        if x < sy.x0 + 0.5 * sp or x > sy.x1 + 0.5 * sp:
            continue
        for s in sy.staves:
            if y0 <= s.top + 0.25 * s.space and y1 >= s.bottom - 0.25 * s.space:
                seg = Segment(x, y0, x, y1, w)
                if not _is_stem(seg, heads, s.space):
                    xs.append((x, w))
                break
    xs.sort()
    clusters: list[list[tuple[float, float]]] = []
    for x, w in xs:
        if clusters and x - clusters[-1][-1][0] < 1.2 * sp:
            clusters[-1].append((x, w))
        else:
            clusters.append([(x, w)])
    out = []
    for cl in clusters:
        uniq = sorted(set(round(c[0], 1) for c in cl))
        thick = any(c[1] > 0.3 * sp for c in cl)
        kind = "single"
        if len(uniq) >= 2:
            kind = "final" if thick else "double"
        elif thick:
            kind = "heavy"
        out.append((min(c[0] for c in cl), max(c[0] for c in cl), kind))
    return out
