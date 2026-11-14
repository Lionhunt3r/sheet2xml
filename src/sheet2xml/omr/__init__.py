"""Recognition of scanned or photographed sheet music."""

from __future__ import annotations

import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .audiveris import OMRError, run_audiveris
from .cleanup import clean_omr
from .combine import ORDER, Combined, combine
from .engines import available_engines, images_to_book, run_engine
from .merge import merge_musicxml
from .preprocess import prepare_file

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"}

NAMES = {"audiveris": "Audiveris", "homr": "homr", "copisteria": "copisteria"}

__all__ = ["OMRError", "Recognition", "available", "chosen_engines", "recognize", "recognize_files"]


@dataclass
class Recognition:
    xml: str
    engines: list[str] = field(default_factory=list)  # recognisers whose reading went into the result
    base: str = ""  # the one whose notes stand unless outvoted
    disputed: list[str] = field(default_factory=list)  # measures no two recognisers agree on, e.g. "12"
    borrowed: dict[str, str] = field(default_factory=dict)  # kind of sign -> recogniser it came from
    notes: list[str] = field(default_factory=list)  # for the user: what failed or was left out


def chosen_engines() -> list[str]:
    """The installed recognisers, best note reader first. SHEET2XML_ENGINES narrows the choice,
    e.g. "homr,audiveris" on a slow machine or "audiveris" for the behaviour before."""
    found = available_engines()
    want = os.environ.get("SHEET2XML_ENGINES", "").strip()
    if want:
        names = [n.strip().lower() for n in want.split(",") if n.strip()]
        found = [n for n in found if n in names]
    return [n for n in ORDER if n in found]


def available() -> bool:
    return bool(chosen_engines())



def _pdf_to_images(pdf: str, out_dir: str, dpi: int = 300) -> list[str]:
    doc = pymupdf.open(pdf)
    paths = []
    for i, page in enumerate(doc):
        p = os.path.join(out_dir, f"page-{i + 1:03d}-raw.png")
        page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).save(p)
        paths.append(p)
    return paths


def _label(v, parts: int) -> str:
    return v.number if parts == 1 else f"{v.number} (Stimme {v.part + 1})"


def recognize(paths: list[str], preprocess: bool = True, review: bool = False, progress=None) -> Recognition:
    """Run OMR on one PDF or several images (the pages of one piece, in order).

    Every installed recogniser reads the same prepared pages; their readings are made into
    one score (combine.py). review: let Claude check measures whose rhythm does not add up."""
    say = progress or (lambda s: None)
    engines = chosen_engines()
    if not engines:
        raise OMRError("Kein Notenerkenner installiert (Audiveris, homr oder copisteria)")
    with tempfile.TemporaryDirectory(prefix="sheet2xml-") as tmp:
        images: list[str] = []
        for path in paths:
            ext = Path(path).suffix.lower()
            if ext == ".pdf":
                images += _pdf_to_images(path, tmp)
            elif ext in IMAGE_EXT:
                images.append(path)
            else:
                raise OMRError(f"Dateityp {ext} wird nicht unterstützt")
        prepared = []
        for i, img in enumerate(images):
            out = os.path.join(tmp, f"page-{i + 1:03d}.png")
            if preprocess:
                photo = not img.endswith("-raw.png")
                prepare_file(img, out, photo=photo)
            else:
                out = img
            prepared.append(out)
        keep = os.path.join(tmp, "omr")
        os.makedirs(keep)
        audiveris_files = 0

        def read(name: str) -> str:
            nonlocal audiveris_files
            if name != "audiveris":
                return run_engine(name, prepared)
            # one PDF holding all pages, so Audiveris treats them as one book
            book = images_to_book(prepared, os.path.join(tmp, "noten.pdf"))
            texts = run_audiveris([book], keep_dir=keep)
            audiveris_files = len(texts)
            return clean_omr(merge_musicxml(texts))

        def attempt(name: str) -> tuple[str | None, str]:
            try:
                return read(name), ""
            except Exception as exc:  # one recogniser failing must not cost the others' work
                log.warning("%s failed: %s", name, exc)
                return None, str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__

        if len(engines) > 1:
            say(f"{len(engines)} Erkenner lesen die Seiten: " + ", ".join(NAMES[n] for n in engines))
        with ThreadPoolExecutor(max_workers=len(engines)) as pool:
            results = dict(zip(engines, pool.map(attempt, engines)))
        readings = {n: xml for n, (xml, _) in results.items() if xml}
        failed = [f"{NAMES[n]} ist ausgefallen: {err}" for n, (xml, err) in results.items() if not xml]
        if not readings:
            raise OMRError("Keine Noten erkannt.\n" + "\n".join(failed))
        combined: Combined = combine(readings)
        parts = combined.xml.count("<part ")
        rec = Recognition(xml=combined.xml, engines=combined.engines, base=combined.base,
                          disputed=[_label(v, parts) for v in combined.disputed], borrowed=combined.borrowed,
                          notes=failed + [n for n in combined.notes])
        if len(combined.engines) > 1:
            say(f"Abgleich: {len(combined.disputed)} Takte strittig, {len(combined.outvoted)} überstimmt")
        projects = sorted(Path(keep).glob("*.omr"))
        if review and "audiveris" in combined.engines and audiveris_files == 1 and len(projects) == 1:
            from .review import review_omr

            # Audiveris knows where each of its measures stands on the page; ours are matched to them
            where = None
            if combined.base != "audiveris":
                where = {(v.part, v.index): v.others["audiveris"] for v in combined.verdicts
                         if v.others.get("audiveris") is not None}
            rec.xml, _ = review_omr(rec.xml, str(projects[0]), prepared, progress=progress, where=where)
        elif review:
            say("Claude-Prüfung übersprungen: dafür muss Audiveris die Seiten mitgelesen haben")
        return rec


def recognize_files(paths: list[str], preprocess: bool = True, review: bool = False, progress=None) -> str:
    return recognize(paths, preprocess=preprocess, review=review, progress=progress).xml
