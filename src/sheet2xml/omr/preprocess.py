"""Clean up photos and scans before optical music recognition.

Phone photos are skewed, shot at an angle, unevenly lit and come in odd
resolutions. Audiveris works best on a flat, evenly lit page whose staff
lines are about 20 pixels apart, so we:

1. find the sheet in the photo and undo the perspective,
2. straighten the remaining small rotation using the staff lines,
3. flatten the lighting (divide by a blurred background),
4. rescale so that the staff spacing is ~20 px.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

TARGET_INTERLINE = 22.0
MAX_PIXELS = 18_000_000  # Audiveris refuses sheets above 20 megapixels


def load_image(path: str) -> np.ndarray:
    """Load any image (incl. HEIC) as an 8-bit grayscale array, honouring EXIF rotation."""
    if Path(path).suffix.lower() in (".heic", ".heif"):
        import pillow_heif

        pillow_heif.register_heif_opener()
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return np.array(im.convert("L"))


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def find_page(gray: np.ndarray) -> np.ndarray | None:
    """Corners of the sheet of paper if it is clearly visible, else None."""
    h, w = gray.shape
    scale = 1000.0 / max(h, w)
    small = cv2.resize(gray, (int(w * scale), int(h * scale)))
    blur = cv2.GaussianBlur(small, (7, 7), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < 0.25 * small.shape[0] * small.shape[1] or area > 0.98 * small.shape[0] * small.shape[1]:
        return None
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, 0.02 * peri, True)
    if len(approx) != 4:
        return None
    return _order_corners(approx.reshape(4, 2).astype(np.float32) / scale)


def warp_page(gray: np.ndarray, corners: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = corners
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(corners, dst)
    return cv2.warpPerspective(gray, m, (width, height), flags=cv2.INTER_CUBIC, borderValue=255)


def flatten_light(gray: np.ndarray) -> np.ndarray:
    """Remove shadows and uneven lighting."""
    k = max(31, (min(gray.shape) // 20) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (k, k), 0)
    norm = cv2.divide(gray, bg, scale=255)
    lo, hi = np.percentile(norm, (1, 99.5))
    if hi - lo < 10:
        return norm
    norm = np.clip((norm.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return norm


def skew_angle(gray: np.ndarray) -> float:
    """Angle (degrees) of the staff lines, measured with a Hough transform."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    w = bw.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 30), 1))
    lines_img = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    lines = cv2.HoughLinesP(lines_img, 1, np.pi / 1800, threshold=100, minLineLength=w // 4, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) < 15:
            angles.append(a)
    return float(np.median(angles)) if angles else 0.0


def rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.05:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def interline(gray: np.ndarray) -> float | None:
    """Estimate the staff spacing in pixels from vertical white runs between black runs."""
    _, bw = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    cols = bw[:, :: max(1, w // 300)]
    runs: dict[int, int] = {}
    blacks: dict[int, int] = {}
    for col in cols.T:
        d = np.diff(np.concatenate(([0], col, [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        for s, e in zip(starts, ends):
            blacks[e - s] = blacks.get(e - s, 0) + 1
        for e, s in zip(ends[:-1], starts[1:]):
            runs[s - e] = runs.get(s - e, 0) + 1
    if not runs or not blacks:
        return None
    white = max((k for k in runs if 2 < k < h // 10), key=lambda k: runs[k], default=None)
    black = max(blacks, key=lambda k: blacks[k])
    if white is None:
        return None
    return float(white + black)


def prepare(gray: np.ndarray, photo: bool = True) -> np.ndarray:
    if photo:
        corners = find_page(gray)
        if corners is not None:
            gray = warp_page(gray, corners)
    gray = flatten_light(gray)
    gray = rotate(gray, skew_angle(gray))
    il = interline(gray)
    if il and il > 3:
        f = TARGET_INTERLINE / il
        f = min(4.0, max(0.4, f))
    else:
        f = 1.0
    h, w = gray.shape
    f = min(f, math.sqrt(MAX_PIXELS / (h * w)))
    if abs(f - 1) > 0.1:
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
    return gray


def prepare_file(path: str, out_path: str, photo: bool = True) -> str:
    gray = load_image(path)
    cv2.imwrite(out_path, prepare(gray, photo=photo))
    return out_path
