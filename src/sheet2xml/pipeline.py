"""Choose the right recogniser for an input and produce MusicXML."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .model import Score
from .musicxml import to_musicxml
from .vector.fonts import is_music_font

log = logging.getLogger(__name__)

PDF_EXT = {".pdf"}


@dataclass
class Result:
    musicxml: str
    engine: str  # "vector" or "omr"
    title: str = ""
    parts: list[str] = field(default_factory=list)
    measures: int = 0
    warnings: list[str] = field(default_factory=list)
    flagged_measures: int = 0
    reviewed: bool = False


def pdf_kind(path: str) -> str:
    """'vector' if the PDF carries music fonts (exported from a notation program), else 'scan'."""
    doc = pymupdf.open(path)
    music_chars = 0
    for page in doc:
        for f in page.get_fonts(full=False):
            name = f[3]
            if is_music_font(name):
                music_chars += 1
        if music_chars:
            break
    if not music_chars:
        return "scan"
    # make sure the glyphs are really there (not only an embedded font)
    for page in doc:
        text = page.get_text("rawdict")
        for b in text["blocks"]:
            for line in b.get("lines", []):
                for span in line["spans"]:
                    if is_music_font(span["font"]) and len(span["chars"]) > 0:
                        return "vector"
    return "scan"


def convert(paths: list[str] | str, engine: str = "auto", review: bool = False, progress=None) -> Result:
    """Convert one PDF or a list of page images into MusicXML.

    engine: "auto" (vector for notation-program PDFs, OMR otherwise), "vector" or "omr".
    review: let Claude double-check measures whose rhythm does not add up.
    progress: optional callback(step: str) for user feedback.
    """
    if isinstance(paths, str):
        paths = [paths]
    say = progress or (lambda s: None)
    first = paths[0]
    ext = Path(first).suffix.lower()
    use = engine
    if engine == "auto":
        use = "vector" if (ext in PDF_EXT and len(paths) == 1 and pdf_kind(first) == "vector") else "omr"
    if use == "vector":
        say("PDF enthält Notensatz-Daten: lese Noten direkt aus dem PDF")
        from .vector.assemble import read_vector_pdf

        try:
            score = read_vector_pdf(first)
        except Exception as exc:  # fall back to OMR on anything unexpected
            log.exception("vector reading failed")
            if engine == "vector":
                raise
            say(f"Direktes Auslesen fehlgeschlagen ({exc}); nutze Notenerkennung")
            return _omr(paths, say, review)
        reviewed = False
        if review:
            from .review import review_score

            say("Claude prüft auffällige Takte")
            reviewed = review_score(score, first, progress=say)
        return _result_from_score(score, reviewed)
    return _omr(paths, say, review)


def _omr(paths: list[str], say, review: bool = False) -> Result:
    from .omr import recognize_files

    say("Bild wird aufbereitet und mit Audiveris erkannt (kann 1–3 Minuten dauern)")
    xml = recognize_files(paths, review=review, progress=say)
    res = _result_from_omr(xml)
    res.reviewed = review
    return res


def _result_from_omr(xml: str) -> Result:
    import xml.etree.ElementTree as ET

    from .omr.review import find_problems

    res = Result(musicxml=xml, engine="omr")
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return res
    res.flagged_measures = len(find_problems(root))
    res.title = (root.findtext("work/work-title") or root.findtext("movement-title") or "").strip()
    res.parts = [(sp.findtext("part-name") or "").strip() or f"Stimme {i + 1}"
                 for i, sp in enumerate(root.iter("score-part"))]
    first = root.find("part")
    res.measures = len(first.findall("measure")) if first is not None else 0
    return res


def _result_from_score(score: Score, reviewed: bool) -> Result:
    flagged = sum(1 for p in score.parts for m in p.measures if m.warnings)
    return Result(
        musicxml=to_musicxml(score),
        engine="vector",
        title=score.title,
        parts=[p.name for p in score.parts],
        measures=max((len(p.measures) for p in score.parts), default=0),
        warnings=list(score.warnings),
        flagged_measures=flagged,
        reviewed=reviewed,
    )
