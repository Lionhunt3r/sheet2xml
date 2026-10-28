"""Pull glyphs, text and vector shapes out of a PDF page."""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .fonts import clean_font_name, font_family, glyph_name


@dataclass
class Glyph:
    name: str  # semantic name, e.g. "head.black"
    x: float  # origin x (left edge of the glyph box)
    y: float  # origin y (baseline = staff position for music fonts)
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    char: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Text:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    origin_y: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def italic(self) -> bool:
        return "italic" in self.font.lower() or "oblique" in self.font.lower()

    @property
    def bold(self) -> bool:
        return "bold" in self.font.lower()


@dataclass
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float  # stroke thickness

    @property
    def is_vertical(self) -> bool:
        return abs(self.x1 - self.x0) < 0.05 * max(1e-6, abs(self.y1 - self.y0)) + 0.01

    @property
    def is_horizontal(self) -> bool:
        return abs(self.y1 - self.y0) < 0.05 * max(1e-6, abs(self.x1 - self.x0)) + 0.01


@dataclass
class Polygon:
    points: list[tuple[float, float]]
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Curve:
    start: tuple[float, float]
    end: tuple[float, float]
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PageContent:
    number: int
    width: float
    height: float
    glyphs: list[Glyph] = field(default_factory=list)
    unknown: list[Glyph] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)


def _pt(p) -> tuple[float, float]:
    return (float(p.x), float(p.y))


def extract_page(page: pymupdf.Page) -> PageContent:
    pc = PageContent(number=page.number, width=page.rect.width, height=page.rect.height)
    raw = page.get_text("rawdict")
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                font = clean_font_name(span["font"])
                fam = font_family(font)
                if fam in ("sonata", "sonata-special", "smufl", "dyn"):
                    for c in span["chars"]:
                        ch = c["c"]
                        if ch.isspace():
                            continue
                        name = glyph_name(font, ch)
                        x0, y0, x1, y1 = c["bbox"]
                        g = Glyph(
                            name=name or "?",
                            x=c["origin"][0],
                            y=c["origin"][1],
                            x0=x0, y0=y0, x1=x1, y1=y1,
                            size=span["size"], font=font, char=ch,
                        )
                        if name:
                            pc.glyphs.append(g)
                        else:
                            pc.unknown.append(g)
                    continue
                text = "".join(c["c"] for c in span["chars"])
                if not text.strip():
                    continue
                # split a span on large gaps so lyrics syllables stay separate
                for chunk in _split_chars(span["chars"], span["size"]):
                    t = "".join(c["c"] for c in chunk).strip()
                    if not t:
                        continue
                    xs0 = min(c["bbox"][0] for c in chunk)
                    xs1 = max(c["bbox"][2] for c in chunk)
                    pc.texts.append(
                        Text(
                            text=t, x0=xs0, y0=span["bbox"][1], x1=xs1, y1=span["bbox"][3],
                            size=span["size"], font=font, origin_y=chunk[0]["origin"][1],
                        )
                    )

    for d in page.get_drawings():
        items = d["items"]
        fill = d.get("fill") is not None
        stroke = d["type"] in ("s", "fs")
        width = d.get("width") or 0.0
        if all(it[0] == "l" for it in items) and fill and len(items) >= 3:
            pts = [_pt(items[0][1])] + [_pt(it[2]) for it in items]
            _add_polygon(pc, pts)
            continue
        if any(it[0] == "c" for it in items):
            pts = []
            for it in items:
                pts.append(_pt(it[1]))
                pts.append(_pt(it[-1]))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            first = _pt(items[0][1])
            # the far end of a closed tie/slur outline is the point with the largest distance
            last = max(pts, key=lambda p: abs(p[0] - first[0]))
            pc.curves.append(Curve(start=first, end=last, x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)))
            continue
        for it in items:
            kind = it[0]
            if kind == "l":
                (x0, y0), (x1, y1) = _pt(it[1]), _pt(it[2])
                if stroke:
                    pc.segments.append(Segment(x0, y0, x1, y1, width))
            elif kind == "re":
                r = it[1]
                if fill:
                    _add_polygon(pc, [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
                elif stroke and (r.x1 - r.x0 < 1.2 or r.y1 - r.y0 < 1.2):
                    _add_polygon(pc, [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
                # other outlined boxes are rehearsal-mark frames and the like
            elif kind == "qu":
                q = it[1]
                pts = [_pt(q.ul), _pt(q.ur), _pt(q.lr), _pt(q.ll)]
                if fill:
                    _add_polygon(pc, pts)
    return pc


def _add_polygon(pc: PageContent, pts: list[tuple[float, float]]) -> None:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    # thin filled rectangles are lines drawn as fills (stems, barlines, staff lines)
    if len(pts) <= 6 and (w < 1.2 or h < 1.2) and _is_axis_rect(pts):
        if w <= h:
            pc.segments.append(Segment((x0 + x1) / 2, y0, (x0 + x1) / 2, y1, w))
        else:
            pc.segments.append(Segment(x0, (y0 + y1) / 2, x1, (y0 + y1) / 2, h))
        return
    pc.polygons.append(Polygon(pts, x0, y0, x1, y1))


def _is_axis_rect(pts) -> bool:
    xs = sorted(set(round(p[0], 1) for p in pts))
    ys = sorted(set(round(p[1], 1) for p in pts))
    return len(xs) <= 2 or len(ys) <= 2


def _split_chars(chars, size):
    out, cur = [], []
    last_x1 = None
    for c in chars:
        if last_x1 is not None and c["bbox"][0] - last_x1 > 0.6 * size:
            out.append(cur)
            cur = []
        cur.append(c)
        last_x1 = c["bbox"][2]
    if cur:
        out.append(cur)
    return out


def extract(path: str) -> list[PageContent]:
    doc = pymupdf.open(path)
    return [extract_page(p) for p in doc]
