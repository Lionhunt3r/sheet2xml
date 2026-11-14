"""Turn the clean pages of a notation-program PDF into pages that look scanned or photographed.

The vector reader knows what such a PDF says, so every degraded page comes with its
answer for free: that is the test set for the recognisers of scans and photos.

Usage: python tools/degrade.py score.pdf out_dir [--kind scan|copy|photo|clean] [--seed 1]

Kinds:
  clean  the page rendered at 300 dpi, nothing else (the ceiling a recogniser can reach)
  scan   flatbed scan of a print: slightly crooked, grey paper, soft, noisy, JPEG
  copy   scan of a photocopy: pure black and white, lines eaten and thickened, specks
  photo  phone photo: seen at an angle on a table, shadow across the page, soft, noisy
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np
import pymupdf

KINDS = ("clean", "scan", "copy", "photo")


def render(pdf: str, dpi: int = 300) -> list[np.ndarray]:
    pages = []
    for page in pymupdf.open(pdf):
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        pages.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy())
    return pages


def _rotate(img: np.ndarray, angle: float, fill: int = 255) -> np.ndarray:
    h, w = img.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=fill)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


def scan(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = _rotate(img, float(rng.uniform(-0.8, 0.8)))
    out = cv2.resize(out, None, fx=2 / 3, fy=2 / 3, interpolation=cv2.INTER_AREA)  # a 200 dpi scan
    out = cv2.GaussianBlur(out, (0, 0), 0.7)
    paper = float(rng.uniform(225, 240))
    ink = float(rng.uniform(25, 55))
    f = out.astype(np.float32) / 255.0
    f = ink + f * (paper - ink)
    f += rng.normal(0, 5.0, f.shape).astype(np.float32)
    return _jpeg(np.clip(f, 0, 255).astype(np.uint8), 65)


def copy(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = _rotate(img, float(rng.uniform(-0.5, 0.5)))
    out = cv2.GaussianBlur(out, (0, 0), 1.1)
    # uneven toner: the threshold wanders across the page, so thin lines break in places
    h, w = out.shape
    wander = cv2.resize(rng.uniform(-28, 28, (6, 5)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    bw = np.where(out.astype(np.float32) < 150 + wander, 0, 255).astype(np.uint8)
    specks = rng.random(bw.shape) < 0.0004
    bw[specks] = 0
    bw = cv2.erode(bw, np.ones((2, 2), np.uint8)) if rng.random() < 0.5 else bw  # erode white = thicker ink
    return bw


def photo(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape
    pad = int(0.08 * w)
    table = int(rng.uniform(40, 90))
    canvas = np.full((h + 2 * pad, w + 2 * pad), table, np.uint8)
    canvas[pad:pad + h, pad:pad + w] = img
    H, W = canvas.shape
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    jitter = rng.uniform(-0.035, 0.035, (4, 2)).astype(np.float32) * np.float32([w, h])
    m = cv2.getPerspectiveTransform(src, src + jitter)
    out = cv2.warpPerspective(canvas, m, (W, H), flags=cv2.INTER_CUBIC, borderValue=table)
    # light falls from one side and a soft shadow lies across a corner
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a = float(rng.uniform(0, 2 * np.pi))
    ramp = ((xx / W - 0.5) * np.cos(a) + (yy / H - 0.5) * np.sin(a))
    light = 0.86 + 0.22 * ramp
    cx, cy = float(rng.uniform(0, W)), float(rng.uniform(0, H))
    shadow = 1.0 - 0.25 * np.exp(-(((xx - cx) / (0.45 * W)) ** 2 + ((yy - cy) / (0.45 * H)) ** 2))
    f = out.astype(np.float32) * light * shadow
    f = cv2.GaussianBlur(f, (0, 0), float(rng.uniform(0.9, 1.5)))
    f += rng.normal(0, 4.0, f.shape).astype(np.float32)
    out = np.clip(f, 0, 255).astype(np.uint8)
    out = cv2.resize(out, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    return _jpeg(out, 70)


def degrade(img: np.ndarray, kind: str, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "clean":
        return img
    return {"scan": scan, "copy": copy, "photo": photo}[kind](img, rng)


def degrade_pdf(pdf: str, out_dir: str, kind: str, seed: int = 1, pages: int | None = None) -> list[str]:
    """Write one image per page; photos as .jpg (they go through the photo preparation), the rest as .png."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, img in enumerate(render(pdf)[:pages]):
        ext = "jpg" if kind == "photo" else "png"
        path = os.path.join(out_dir, f"page-{i + 1:03d}.{ext}")
        cv2.imwrite(path, degrade(img, kind, seed + i))
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--kind", choices=KINDS, default="scan")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    for p in degrade_pdf(args.pdf, args.out_dir, args.kind, args.seed):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
