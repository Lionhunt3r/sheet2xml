"""Let Claude double-check measures whose rhythm does not add up.

The vector reader knows exactly where every symbol is, but rare layouts
(cross-staff beams, unusual tuplets, overlapping voices) can still confuse
its rules. Those measures are flagged because their voices do not fill the
time signature. For each flagged measure we crop the measure from the PDF,
show Claude the image together with our reading, and accept Claude's
transcription only if it is rhythmically consistent.

Needs ANTHROPIC_API_KEY (or another credential the Anthropic SDK finds).
"""

from __future__ import annotations

import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field

from .model import Event, Measure, Pitch, Score

log = logging.getLogger(__name__)

MODEL = os.environ.get("SHEET2XML_MODEL", "claude-opus-5-5")
MAX_MEASURES = int(os.environ.get("SHEET2XML_REVIEW_MAX", "60"))

DUR = {
    "whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1), "eighth": Fraction(1, 2),
    "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16),
}
TUPLETS = {"none": None, "triplet": (3, 2), "duplet": (2, 3), "quadruplet": (4, 6),
           "quintuplet": (5, 4), "sextuplet": (6, 4)}


class NoteOut(BaseModel):
    rest: bool = Field(description="true for a rest")
    pitches: list[str] = Field(description="written pitches like 'F#4', 'Bb3', 'C5'; empty for rests")
    duration: Literal["whole", "half", "quarter", "eighth", "16th", "32nd", "64th"]
    dots: int = Field(ge=0, le=2)
    tuplet: Literal["none", "triplet", "duplet", "quadruplet", "quintuplet", "sextuplet"]
    voice: Literal[1, 2] = Field(description="1 = upper/stems up voice or the only voice, 2 = lower voice")
    staff: int = Field(ge=1, le=4, description="staff number from the top inside the instrument")
    whole_measure_rest: bool = Field(description="true if this rest fills the whole measure")
    lyric: str = Field(default="", description="the lyric syllable printed under this note, if any")


class MeasureFix(BaseModel):
    reading_is_correct: bool
    notes: list[NoteOut] = Field(description="all notes and rests of the measure in time order per voice")
    comment: str


SYSTEM = (
    "You are an expert music engraver checking an automatic transcription of one measure of printed sheet music. "
    "You get an image of the measure (several staves if the instrument has more than one) and the machine reading. "
    "Transcribe exactly what is printed: written pitches with the accidentals that apply (key signature, earlier "
    "accidentals in the same measure, ties), note values, dots, tuplets and voices. Every voice must add up to the "
    "length of the measure given by the time signature, unless it is the pickup measure. "
    "If the machine reading is already right, say so and repeat it."
)


def _clef_name(clef) -> str:
    if clef is None:
        return "treble"
    if clef.sign == "F":
        return "bass"
    if clef.sign == "C":
        return {3: "alto (C clef on 3rd line)", 4: "tenor (C clef on 4th line)"}.get(clef.line, "C clef")
    if clef.sign == "percussion":
        return "percussion"
    return "treble 8vb" if clef.octave_change == -1 else "treble"


def _fmt_event(e: Event) -> str:
    if e.is_rest:
        what = "rest"
    else:
        what = "+".join(f"{p.step}{'#' * p.alter if p.alter > 0 else 'b' * -p.alter}{p.octave}" for p in e.pitches)
    return f"[staff {e.staff} voice {e.voice}] {what} {e.duration} quarter(s)" + (" tuplet" if e.tuplet else "")


def _crop(doc: pymupdf.Document, geoms) -> bytes:
    page_no = geoms[0][1]
    page = doc[page_no]
    x0 = min(g[2] for g in geoms)
    x1 = max(g[3] for g in geoms)
    sp = geoms[0][6]
    y0 = min(g[4] for g in geoms) - 6 * sp
    y1 = max(g[5] for g in geoms) + 6 * sp
    clip = pymupdf.Rect(max(0, x0 - 2 * sp), max(0, y0), min(page.rect.width, x1 + 1 * sp), min(page.rect.height, y1))
    return page.get_pixmap(dpi=300, clip=clip).tobytes("png")


def _parse_pitch(s: str) -> Pitch | None:
    s = s.strip()
    if len(s) < 2 or s[0].upper() not in "CDEFGAB":
        return None
    step = s[0].upper()
    rest = s[1:]
    alter = 0
    while rest and rest[0] in "#b♯♭x":
        alter += {"#": 1, "♯": 1, "x": 2, "b": -1, "♭": -1}[rest[0]]
        rest = rest[1:]
    try:
        octave = int(rest)
    except ValueError:
        return None
    return Pitch(step=step, octave=octave, alter=alter)


def events_from_fix(fix: MeasureFix, expected: Fraction, staves: int) -> list[Event] | None:
    """Claude's transcription as events, or None unless every voice fills the measure."""
    events: list[Event] = []
    x = 0.0
    for n in fix.notes:
        base = DUR[n.duration]
        tup = TUPLETS[n.tuplet]
        d = base
        add = base
        for _ in range(n.dots):
            add /= 2
            d += add
        if tup:
            d = d * Fraction(tup[1], tup[0])
        staff = min(max(1, n.staff), staves)
        voice = n.voice + 4 * (staff - 1)
        if n.rest and n.whole_measure_rest:
            d = expected
        pitches = [] if n.rest else [p for p in (_parse_pitch(s) for s in n.pitches) if p]
        if not n.rest and not pitches:
            return None
        events.append(Event(x=x, duration=d, pitches=pitches, voice=voice, staff=staff, base=base, dots=n.dots,
                            tuplet=tup, full_measure=bool(n.rest and n.whole_measure_rest),
                            lyrics=[n.lyric] if n.lyric and not n.rest else []))
        x += 1
    if not events:
        return None
    for v in {e.voice for e in events}:
        total = sum(e.duration for e in events if e.voice == v)
        if total != expected:
            return None
    return events


def _apply(m: Measure, fix: MeasureFix, staves: int) -> bool:
    events = events_from_fix(fix, m.expected or Fraction(4), staves)
    if events is None:
        return False
    # keep ties that the vector reader saw on matching pitches
    old_start = {(p.step, p.octave) for e in m.events for p in e.pitches if p.tie_start}
    old_stop = {(p.step, p.octave) for e in m.events for p in e.pitches if p.tie_stop}
    for v in {e.voice for e in events}:
        ves = [e for e in events if e.voice == v and not e.is_rest]
        if ves:
            for p in ves[-1].pitches:
                p.tie_start = (p.step, p.octave) in old_start
            for p in ves[0].pitches:
                p.tie_stop = (p.step, p.octave) in old_stop
    m.events = events
    m.warnings = [w for w in m.warnings if not w.startswith("voice")] + ["von Claude korrigiert"]
    return True


def _ask(client, image: bytes, part_name: str, m: Measure, staves: int, clefs: list[str], key, time) -> MeasureFix:
    reading = "\n".join(_fmt_event(e) for e in sorted(m.events, key=lambda e: (e.staff, e.voice, e.x)))
    info = (
        f"Instrument: {part_name} ({staves} staff/staves). Clef(s): {', '.join(clefs)}. "
        f"Key signature: {key} (positive = sharps, negative = flats). Time signature: {time[0]}/{time[1]} "
        f"= {m.expected} quarter notes per measure. The measure is the one between the barlines in the middle of "
        f"the image (a bit of the neighbouring measures may be visible at the edges).\n\n"
        f"Machine reading (durations in quarter notes):\n{reading or '(nothing)'}\n\n"
        f"Problems found: {'; '.join(m.warnings)}"
    )
    return ask_claude(client, image, info)


def ask_claude(client, image: bytes, info: str) -> MeasureFix:
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "high"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(image).decode()}},
                {"type": "text", "text": info},
            ],
        }],
        output_format=MeasureFix,
        # on a safety decline, let the API retry on its default fallback model
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("keine Antwort")
    return resp.parsed_output


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def review_score(score: Score, pdf_path: str, progress=None) -> bool:
    """Review flagged measures in place. Returns True if Claude was consulted."""
    say = progress or (lambda s: None)
    if not available():
        say("Claude-Prüfung übersprungen: kein ANTHROPIC_API_KEY gesetzt")
        return False
    import anthropic

    client = anthropic.Anthropic()
    doc = pymupdf.open(pdf_path)
    jobs = []
    for p in score.parts:
        key = 0
        time = (4, 4)
        for m in p.measures:
            key = m.key if m.key is not None else key
            time = m.time or time
            if m.warnings and getattr(m, "_geoms", None) and any(w.startswith("voice") for w in m.warnings):
                if m.number == 1:
                    total = max((sum(e.duration for e in m.events if e.voice == v) for v in {e.voice for e in m.events}),
                                default=0)
                    if total and total < (m.expected or 4):
                        continue  # pickup measure
                jobs.append((p, m, key, time))
    if not jobs:
        return False
    if len(jobs) > MAX_MEASURES:
        say(f"{len(jobs)} auffällige Takte, Claude prüft die ersten {MAX_MEASURES}")
        jobs = jobs[:MAX_MEASURES]
    else:
        say(f"Claude prüft {len(jobs)} auffällige Takte")

    # render crops up front: PyMuPDF documents are not thread-safe
    images = [_crop(doc, sorted(m._geoms)) for _, m, _, _ in jobs]  # type: ignore[attr-defined]

    def work(arg):
        (p, m, key, time), image = arg
        geoms = sorted(m._geoms)  # type: ignore[attr-defined]
        clefs = [_clef_name(g[7]) for g in geoms]
        try:
            fix = _ask(client, image, p.name, m, p.staves, clefs, key, time)
        except Exception as exc:  # network, refusal, validation: keep our reading
            log.warning("review of measure %s failed: %s", m.number, exc)
            return False
        if fix.reading_is_correct:
            return False
        return _apply(m, fix, p.staves)

    with ThreadPoolExecutor(max_workers=6) as pool:
        fixed = sum(1 for ok in pool.map(work, zip(jobs, images)) if ok)
    say(f"Claude hat {fixed} von {len(jobs)} Takten korrigiert")
    return True
