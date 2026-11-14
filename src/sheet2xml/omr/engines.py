"""The recognisers that can read a prepared page image.

Besides Audiveris two neural recognisers can be installed next to sheet2xml:

- homr (https://github.com/liebharc/homr, AGPL): `pip install "homr[cpu]"` into its own
  environment; point HOMR_BIN at its `homr` command if it is not on the PATH.
- copisteria (https://github.com/kobimusic/copisteria, Apache 2.0): a checkout with its
  weights under `models/`; point COPISTERIA_HOME at the checkout and COPISTERIA_PYTHON at
  the Python of the environment it is installed in.

Each lives in its own environment and is called as a separate program, so their
dependencies (onnxruntime, torch) never meet ours. Every recogniser gets the same
prepared images and gives back one MusicXML text for the whole piece.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from .audiveris import OMRError, audiveris_binary, run_audiveris
from .cleanup import clean_homr
from .merge import merge_musicxml

ENGINES = ("audiveris", "homr", "copisteria")


def _homr_binary() -> str | None:
    cand = os.environ.get("HOMR_BIN") or shutil.which("homr")
    return cand if cand and os.path.exists(cand) else None


def _copisteria() -> tuple[str, str] | None:
    home = os.environ.get("COPISTERIA_HOME", "/opt/copisteria")
    python = os.environ.get("COPISTERIA_PYTHON", os.path.join(home, ".venv", "bin", "python"))
    if os.path.isdir(os.path.join(home, "models")) and os.path.exists(python):
        return home, python
    return None


def available_engines() -> list[str]:
    found = []
    if audiveris_binary():
        found.append("audiveris")
    if _homr_binary():
        found.append("homr")
    if _copisteria():
        found.append("copisteria")
    return found


def images_to_book(images: list[str], out_pdf: str) -> str:
    """One PDF holding all pages, sized so that a 300 dpi rendering gives back the pixels 1:1."""
    doc = pymupdf.open()
    for img in images:
        pix = pymupdf.Pixmap(img)
        page = doc.new_page(width=pix.width * 72 / 300, height=pix.height * 72 / 300)
        page.insert_image(page.rect, filename=img)
    doc.save(out_pdf)
    return out_pdf


def _run(cmd: list[str], timeout: int, cwd: str | None = None, env: dict | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    return "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])


def run_homr(images: list[str], timeout: int = 900) -> str:
    binary = _homr_binary()
    if not binary:
        raise OMRError("homr ist nicht installiert (HOMR_BIN setzen)")
    texts = []
    with tempfile.TemporaryDirectory(prefix="homr-") as tmp:
        for i, img in enumerate(images):
            page = os.path.join(tmp, f"page-{i + 1:03d}.png")
            shutil.copy(img, page)
            tail = _run([binary, page], timeout)
            out = Path(page).with_suffix(".musicxml")
            if not out.exists():
                raise OMRError(f"homr hat auf Seite {i + 1} keine Noten erkannt.\n{tail}")
            texts.append(out.read_text(encoding="utf-8"))
    return merge_musicxml(texts)


def run_copisteria(images: list[str], timeout: int = 900) -> str:
    found = _copisteria()
    if not found:
        raise OMRError("copisteria ist nicht installiert (COPISTERIA_HOME und COPISTERIA_PYTHON setzen)")
    home, python = found
    texts = []
    with tempfile.TemporaryDirectory(prefix="copisteria-") as tmp:
        pages = []
        for i, img in enumerate(images):
            page = os.path.join(tmp, f"page-{i + 1:03d}.png")
            shutil.copy(img, page)
            pages.append(page)
        out = os.path.join(tmp, "out")
        # the reader finds its weights relative to the checkout
        tail = _run([python, "-m", "copisteria.pipeline", *pages, "--out", out], timeout * len(pages), cwd=home)
        for i, page in enumerate(pages):
            f = Path(out) / (Path(page).stem + ".musicxml")
            if not f.exists():
                raise OMRError(f"copisteria hat auf Seite {i + 1} keine Noten erkannt.\n{tail}")
            texts.append(f.read_text(encoding="utf-8"))
    return merge_musicxml(texts)


def run_engine(name: str, images: list[str], keep_dir: str | None = None, **options) -> str:
    """MusicXML of the piece on these prepared page images, read by one recogniser.

    keep_dir (Audiveris only): where to keep its project file with the page geometry."""
    if name == "audiveris":
        with tempfile.TemporaryDirectory(prefix="sheet2xml-book-") as tmp:
            book = images_to_book(images, os.path.join(tmp, "noten.pdf"))
            return merge_musicxml(run_audiveris([book], keep_dir=keep_dir, **options))
    if name == "homr":
        return clean_homr(run_homr(images))
    if name == "copisteria":
        return run_copisteria(images)
    raise OMRError(f"Unbekannter Erkenner: {name}")
