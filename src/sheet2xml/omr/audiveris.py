"""Optical music recognition with Audiveris (https://github.com/Audiveris/audiveris).

Audiveris is a Java program; we call its command line in batch mode and read
the MusicXML it exports. Several movements or sheets can produce several
files, which we merge part-wise.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

CANDIDATES = [
    os.environ.get("AUDIVERIS_BIN", ""),
    "/opt/audiveris/bin/Audiveris",
    "/Applications/Audiveris.app/Contents/MacOS/Audiveris",
    shutil.which("audiveris") or "",
    shutil.which("Audiveris") or "",
]


class OMRError(RuntimeError):
    pass


def audiveris_binary() -> str | None:
    for c in CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


# Audiveris needs the full "tessdata" models (legacy + LSTM), not tessdata_fast
TESSDATA_CANDIDATES = ["/opt/tessdata", "/opt/homebrew/share/tessdata", "/usr/local/share/tessdata",
                       *sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata"))]


def _tessdata() -> str | None:
    for d in TESSDATA_CANDIDATES:
        if os.path.exists(os.path.join(d, "eng.traineddata")):
            return d
    return None


def available() -> bool:
    return audiveris_binary() is not None


def read_mxl(path: str) -> str:
    """Return the MusicXML text inside a compressed .mxl container."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        root = None
        if "META-INF/container.xml" in names:
            cont = z.read("META-INF/container.xml").decode("utf-8", "replace")
            m = re.search(r'full-path="([^"]+)"', cont)
            if m:
                root = m.group(1)
        if root is None:
            root = next(n for n in names if n.endswith(".xml") and not n.startswith("META-INF"))
        return z.read(root).decode("utf-8")


def run_audiveris(inputs: list[str], timeout: int = 900) -> list[str]:
    """Run Audiveris on images/PDFs; returns MusicXML strings (one per exported file)."""
    binary = audiveris_binary()
    if not binary:
        raise OMRError("Audiveris ist nicht installiert (AUDIVERIS_BIN setzen)")
    out_dir = tempfile.mkdtemp(prefix="audiveris-")
    try:
        # lyrics and chord symbols are read with Tesseract (German + English)
        cmd = [binary, "-batch", "-export", "-output", out_dir,
               "-constant", "org.audiveris.omr.text.Language.defaultSpecification=deu+eng", "--", *inputs]
        env = dict(os.environ)
        env.setdefault("JAVA_TOOL_OPTIONS", "")
        env["JAVA_TOOL_OPTIONS"] += " -Djava.awt.headless=true"
        if "TESSDATA_PREFIX" not in env and (tessdata := _tessdata()):
            env["TESSDATA_PREFIX"] = tessdata
        # lyrics and chord symbols are read with Tesseract (German + English)
        cmd = [binary, "-batch", "-export", "-output", out_dir,
               "-constant", "org.audiveris.omr.text.Language.defaultSpecification=deu+eng", "--", *inputs]
        env = dict(os.environ)
        env.setdefault("JAVA_TOOL_OPTIONS", "")
        env["JAVA_TOOL_OPTIONS"] += " -Djava.awt.headless=true"
        if "TESSDATA_PREFIX" not in env:
            found = sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata")) + ["/opt/tessdata"] * os.path.isdir("/opt/tessdata") + sorted(
                glob.glob("/opt/homebrew/share/tessdata")) + sorted(glob.glob("/usr/local/share/tessdata"))
            if found:
                env["TESSDATA_PREFIX"] = found[-1]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        files = sorted(glob.glob(os.path.join(out_dir, "**", "*.mxl"), recursive=True))
        files += sorted(glob.glob(os.path.join(out_dir, "**", "*.xml"), recursive=True))
        if not files:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            raise OMRError(f"Audiveris hat keine Noten erkannt.\n{tail}")
        texts = []
        for f in files:
            texts.append(read_mxl(f) if f.endswith(".mxl") else Path(f).read_text(encoding="utf-8"))
        return texts
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
