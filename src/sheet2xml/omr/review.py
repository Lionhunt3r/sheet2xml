"""Let Claude check measures of a scan or photo whose rhythm does not add up.

Audiveris keeps the geometry of everything it recognised in its project file
(.omr): for every measure the left and right edge and the staff lines of each
part, in the pixels of the page image it read. Together with the MusicXML it
exported we can find measures whose voices do not fill the time signature,
crop exactly that measure from the page and show it to Claude next to the
machine reading. A correction is taken over only if it fills the measure.
"""

from __future__ import annotations

import io
import logging
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from fractions import Fraction

from PIL import Image

from ..model import Measure, Part, Score

log = logging.getLogger(__name__)

@dataclass
class MeasureBox:
    """Where one exported measure sits on its page image."""

    sheet: int  # 0-based page
    left: float
    right: float
    parts: list[tuple[float, float] | None]  # (top, bottom) of each part's staves
    interline: float


@dataclass
class Problem:
    part: int
    index: int  # measure index inside the part
    measure: ET.Element
    divisions: int
    expected: Fraction  # in quarter notes
    staves: int
    clefs: dict[int, str] = field(default_factory=dict)
    key: int = 0
    time: tuple[int, int] = (4, 4)
    totals: dict[str, Fraction] = field(default_factory=dict)


def _sheet_no(name: str) -> int:
    m = re.search(r"#(\d+)", name)
    return int(m.group(1)) if m else 0


def read_layout(omr_path: str) -> list[MeasureBox]:
    """One box per exported measure, in score order (multi-measure rests repeat their box)."""
    boxes: list[MeasureBox] = []
    with zipfile.ZipFile(omr_path) as z:
        sheets = sorted({n.split("/")[0] for n in z.namelist() if re.match(r"sheet#\d+/sheet#\d+\.xml$", n)},
                        key=_sheet_no)
        for si, sh in enumerate(sheets):
            root = ET.fromstring(z.read(f"{sh}/{sh}.xml"))
            il_el = root.find("scale/interline")
            il = float(il_el.get("main", 20)) if il_el is not None else 20.0
            for page in root.iter("page"):
                count = int(page.get("measure-count", "0"))
                stacks = []
                for system in page.iter("system"):
                    parts: list[tuple[float, float] | None] = []
                    for part in system.findall("part"):
                        ys = [float(pt.get("y")) for pt in part.iter("point") if pt.get("y")]
                        parts.append((min(ys), max(ys)) if ys else None)
                    for st in system.findall("stack"):
                        stacks.append((int(st.get("id")), float(st.get("left")), float(st.get("right")), parts))
                for i, (sid, left, right, parts) in enumerate(stacks):
                    # a multi-measure rest is one stack standing for several measures
                    if i + 1 < len(stacks):
                        n = stacks[i + 1][0] - sid
                    else:
                        n = count - sum(max(1, stacks[k + 1][0] - stacks[k][0]) for k in range(len(stacks) - 1))
                    for _ in range(max(1, n)):
                        boxes.append(MeasureBox(sheet=si, left=left, right=right, parts=parts, interline=il))
    return boxes


def _clef_name(sign: str, line: int, octave: int) -> str:
    if sign == "F":
        return "bass"
    if sign == "C":
        return {3: "alto (C clef on 3rd line)", 4: "tenor (C clef on 4th line)"}.get(line, "C clef")
    if sign == "percussion":
        return "percussion"
    return "treble 8vb" if octave == -1 else "treble"


def find_problems(root: ET.Element) -> list[Problem]:
    """Measures in which a voice does not add up to the time signature."""
    out: list[Problem] = []
    for pi, part in enumerate(root.findall("part")):
        divisions = 1
        time = (4, 4)
        key = 0
        staves = 1
        clefs: dict[int, str] = {}
        for mi, m in enumerate(part.findall("measure")):
            for att in m.findall("attributes"):
                if att.findtext("divisions"):
                    divisions = int(float(att.findtext("divisions")))
                if att.find("time") is not None and att.findtext("time/beats"):
                    try:
                        time = (int(att.findtext("time/beats")), int(att.findtext("time/beat-type")))
                    except (TypeError, ValueError):
                        pass
                if att.findtext("key/fifths"):
                    key = int(att.findtext("key/fifths"))
                if att.findtext("staves"):
                    staves = int(att.findtext("staves"))
                for c in att.findall("clef"):
                    clefs[int(c.get("number", "1"))] = _clef_name(
                        c.findtext("sign") or "G", int(c.findtext("line") or 2),
                        int(c.findtext("clef-octave-change") or 0))
            expected = Fraction(time[0] * 4, time[1])
            totals: dict[str, int] = {}
            for el in m:
                if el.tag == "note":
                    if el.find("chord") is not None or el.find("grace") is not None:
                        continue
                    v = el.findtext("voice") or "1"
                    totals[v] = totals.get(v, 0) + int(float(el.findtext("duration") or 0))
                elif el.tag == "forward" and el.findtext("voice"):
                    v = el.findtext("voice")
                    totals[v] = totals.get(v, 0) + int(float(el.findtext("duration") or 0))
            if m.get("implicit") == "yes" or not totals:
                continue
            if m.find("attributes/measure-style/multiple-rest") is not None:
                continue
            want = expected * divisions
            if any(t != want for t in totals.values()):
                out.append(Problem(part=pi, index=mi, measure=m, divisions=divisions, expected=expected,
                                   staves=staves, clefs=dict(clefs), key=key, time=time,
                                   totals={v: Fraction(t, divisions) for v, t in totals.items()}))
    return out


def _reading(p: Problem) -> str:
    lines = []
    for el in p.measure.findall("note"):
        pitch = el.find("pitch")
        if pitch is not None:
            alter = int(float(pitch.findtext("alter") or 0))
            what = f"{pitch.findtext('step')}{'#' * alter if alter > 0 else 'b' * -alter}{pitch.findtext('octave')}"
        elif el.find("unpitched") is not None:
            what = "unpitched"
        else:
            what = "rest"
        typ = el.findtext("type") or ""
        dots = len(el.findall("dot"))
        tm = el.find("time-modification")
        tup = f" tuplet {tm.findtext('actual-notes')}:{tm.findtext('normal-notes')}" if tm is not None else ""
        lyr = el.findtext("lyric/text")
        extra = f' lyric "{lyr}"' if lyr else ""
        grace = " grace" if el.find("grace") is not None else ""
        if el.find("chord") is not None and lines:
            lines[-1] = lines[-1].replace(" |", f"+{what} |", 1)
            continue
        lines.append(f"[staff {el.findtext('staff') or 1} voice {el.findtext('voice') or 1}] {what} | "
                     f"{typ}{' dotted' * dots}{tup}{grace}{extra}")
    return "\n".join(lines)


def _crop(image: Image.Image, box: MeasureBox, part: int) -> bytes | None:
    if part >= len(box.parts) or box.parts[part] is None:
        return None
    top, bottom = box.parts[part]
    il = box.interline
    x0 = max(0, int(box.left - 1.5 * il))
    x1 = min(image.width, int(box.right + 1.0 * il))
    y0 = max(0, int(top - 5 * il))
    y1 = min(image.height, int(bottom + 6 * il))
    crop = image.crop((x0, y0, x1, y1))
    if crop.width * crop.height > 4_000_000:
        f = math.sqrt(4_000_000 / (crop.width * crop.height))
        crop = crop.resize((int(crop.width * f), int(crop.height * f)))
    buf = io.BytesIO()
    crop.convert("L").save(buf, "PNG")
    return buf.getvalue()


def _scale_part(part: ET.Element, k: int) -> None:
    """Multiply all durations of a part by k (to make room for tuplets)."""
    for el in part.iter():
        if el.tag in ("duration", "divisions") and el.text:
            el.text = str(int(float(el.text)) * k)


def replace_measure(part: ET.Element, p: Problem, events, staves: int) -> bool:
    """Swap the notes of a measure for Claude's transcription (lyrics of the old reading are kept)."""
    from ..musicxml import to_musicxml

    mini = Score(parts=[Part(name="x", staves=staves,
                             measures=[Measure(number=1, events=events, expected=p.expected, time=p.time)])])
    mroot = ET.fromstring(to_musicxml(mini).split("\n", 2)[2].encode("utf-8"))
    mmeasure = mroot.find("part/measure")
    mdiv = int(mroot.findtext("part/measure/attributes/divisions") or 1)
    new = [el for el in mmeasure if el.tag in ("note", "backup", "forward")]
    # durations in the target part's divisions; grow the part's divisions if they do not fit
    durs = [Fraction(int(el.findtext("duration")), mdiv) for el in new if el.findtext("duration")]
    need = 1
    for d in durs:
        q = d * p.divisions
        need = need * q.denominator // math.gcd(need, q.denominator)
    if need > 1:
        _scale_part(part, need)
        p.divisions *= need
    for el in new:
        d = el.find("duration")
        if d is not None:
            d.text = str(int(Fraction(int(d.text), mdiv) * p.divisions))
    # keep the lyrics that were read before, in order
    old_lyrics = [el.findall("lyric") for el in p.measure.findall("note")
                  if el.find("chord") is None and el.find("rest") is None and el.findall("lyric")]
    if old_lyrics:
        for el in new:
            for ly in el.findall("lyric"):
                el.remove(ly)
        targets = [el for el in new if el.tag == "note" and el.find("chord") is None and el.find("rest") is None]
        for el, lys in zip(targets, old_lyrics):
            for ly in lys:
                el.append(ly)
    children = list(p.measure)
    pos = next((i for i, el in enumerate(children) if el.tag in ("note", "backup", "forward")), None)
    for el in children:
        if el.tag in ("note", "backup", "forward"):
            p.measure.remove(el)
    if pos is None:
        pos = len([el for el in p.measure if el.tag != "barline"])
    else:
        pos = min(pos, len(list(p.measure)))
    for i, el in enumerate(new):
        p.measure.insert(pos + i, el)
    return True


def review_omr(xml: str, omr_path: str, images: list[str], progress=None, client=None) -> tuple[str, int]:
    """Check suspicious measures with Claude. Returns (musicxml, corrected measures)."""
    from ..review import MAX_MEASURES, ask_claude, available, events_from_fix

    say = progress or (lambda s: None)
    if client is None:
        if not available():
            say("Claude-Prüfung übersprungen: kein ANTHROPIC_API_KEY gesetzt")
            return xml, 0
        import anthropic

        client = anthropic.Anthropic()
    header, _, body = xml.partition("<score-partwise")
    root = ET.fromstring(("<score-partwise" + body).encode("utf-8"))
    problems = find_problems(root)
    if not problems:
        return xml, 0
    boxes = read_layout(omr_path)
    parts = root.findall("part")
    n_measures = len(parts[0].findall("measure")) if parts else 0
    if len(boxes) != n_measures:
        log.warning("Audiveris layout has %d measures, MusicXML %d: no review", len(boxes), n_measures)
        say("Claude-Prüfung übersprungen: Takte ließen sich dem Bild nicht zuordnen")
        return xml, 0
    names = [(sp.findtext("part-name") or "").strip() for sp in root.iter("score-part")]
    if len(problems) > MAX_MEASURES:
        say(f"{len(problems)} auffällige Takte, Claude prüft die ersten {MAX_MEASURES}")
        problems = problems[:MAX_MEASURES]
    else:
        say(f"Claude prüft {len(problems)} auffällige Takte")
    pages = [Image.open(p) for p in images]
    jobs = []
    for p in problems:
        box = boxes[p.index]
        if box.sheet >= len(pages):
            continue
        crop = _crop(pages[box.sheet], box, p.part)
        if crop:
            jobs.append((p, crop))

    def work(job):
        p, image = job
        name = names[p.part] if p.part < len(names) else f"Stimme {p.part + 1}"
        clefs = ", ".join(p.clefs[s] for s in sorted(p.clefs)) or "treble"
        found = "; ".join(f"voice {v} lasts {t} instead of {p.expected} quarter notes" for v, t in p.totals.items()
                          if t != p.expected)
        info = (
            f"This measure comes from a photo or scan, read by optical music recognition, which often misses "
            f"or invents notes, dots, flags, beams, accidentals and rests. Instrument: {name} ({p.staves} staff/staves). "
            f"Clef(s): {clefs}. Key signature: {p.key} (positive = sharps, negative = flats). "
            f"Time signature: {p.time[0]}/{p.time[1]} = {p.expected} quarter notes per measure. The measure is the "
            f"one between the barlines in the middle of the image (parts of the neighbouring measures and of other "
            f"staves may be visible at the edges). Give the lyric syllable under each note if there is one, with a "
            f"trailing '-' when the word continues.\n\n"
            f"Machine reading:\n{_reading(p) or '(nothing)'}\n\nProblems found: {found}"
        )
        try:
            fix = ask_claude(client, image, info)
        except Exception as exc:  # network, refusal, validation: keep the machine reading
            log.warning("review of measure %s failed: %s", p.index, exc)
            return None
        if fix.reading_is_correct:
            return None
        events = events_from_fix(fix, p.expected, p.staves)
        return (p, events) if events else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(work, jobs))
    fixed = 0
    for r in results:
        if r is None:
            continue
        p, events = r
        if replace_measure(parts[p.part], p, events, p.staves):
            fixed += 1
    say(f"Claude hat {fixed} von {len(jobs)} Takten korrigiert")
    out = header + ET.tostring(root, encoding="unicode")
    return out, fixed
