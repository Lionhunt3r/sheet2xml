"""Recognition of scanned or photographed sheet music."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pymupdf

from .audiveris import OMRError, available, run_audiveris
from .merge import merge_musicxml
from .preprocess import prepare_file

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"}

__all__ = ["OMRError", "available", "recognize_files"]


def _pdf_to_images(pdf: str, out_dir: str, dpi: int = 300) -> list[str]:
    doc = pymupdf.open(pdf)
    paths = []
    for i, page in enumerate(doc):
        p = os.path.join(out_dir, f"page-{i + 1:03d}-raw.png")
        page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).save(p)
        paths.append(p)
    return paths


def recognize_files(paths: list[str], preprocess: bool = True) -> str:
    """Run OMR on one PDF or several images (the pages of one piece, in order)."""
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
        # one PDF holding all pages, so Audiveris treats them as one book; the
        # page size is chosen so that Audiveris' 300 dpi rendering gives back
        # the prepared pixels 1:1
        book = os.path.join(tmp, "noten.pdf")
        doc = pymupdf.open()
        for img in prepared:
            pix = pymupdf.Pixmap(img)
            page = doc.new_page(width=pix.width * 72 / 300, height=pix.height * 72 / 300)
            page.insert_image(page.rect, filename=img)
        doc.save(book)
        texts = run_audiveris([book])
        return merge_musicxml(texts)
