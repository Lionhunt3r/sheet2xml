"""Measure the recognisers of scans and photos on a fixed set of pieces.

Usage:
  python tools/scanbench.py cases.json out_dir [--engines audiveris,homr,copisteria]
                                               [--kinds scan,copy,photo] [--jobs 2]

cases.json is a list of pieces:
  {"name": "bach", "pdf": "tests/data/bach_bwv66_6.pdf", "reference": "tests/data/bach_bwv66_6.musicxml"}
  {"name": "heilige-nacht-v1", "pdf": "/data/Heilige Nacht - Violine I.pdf", "pages": 2}
  {"name": "he-keeps-v1", "pdf": "/data/44 Violine 1.pdf", "pages": 1, "expected_marks": {"tremolos": 13}}

- A PDF from a notation program is rendered and degraded (tools/degrade.py) into the given
  kinds of page images. The answer is "reference" if given, otherwise what our vector reader
  reads from the same pages.
- A scanned PDF is read as it is (kind "real"). There is no answer to compare the notes with;
  "expected_marks" can hold signs counted by hand on the page.
- "pages" limits a piece to its first pages (answer and images alike).

Every recogniser reads the same prepared images. Per piece, kind and recogniser the notes are
compared with the answer (tools/evaluate.py), the signs are counted, and the measures whose
rhythm does not add up are counted. Results go to out_dir/results.json and results.md;
recognised files are kept in out_dir and reused on the next run.

Recognisers: audiveris (with our processing switches), audiveris-plain (as shipped),
homr, copisteria (see src/sheet2xml/omr/engines.py for how to install the last two), and
combined: the three readings made into one (src/sheet2xml/omr/combine.py); name it last.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import pymupdf

sys.path.insert(0, os.path.dirname(__file__))

from degrade import degrade, render  # noqa: E402
from evaluate import count_marks, read_xml, score  # noqa: E402

from sheet2xml.omr.cleanup import clean_omr  # noqa: E402
from sheet2xml.omr.combine import ORDER, combine  # noqa: E402
from sheet2xml.omr.engines import run_engine  # noqa: E402
from sheet2xml.omr.preprocess import prepare_file  # noqa: E402
from sheet2xml.omr.review import find_problems  # noqa: E402
from sheet2xml.pipeline import convert, pdf_kind  # noqa: E402

MARKS = ("dynamics", "hairpins", "slurs", "tremolos", "articulations", "tuplet_notes")


def first_pages(pdf: str, pages: int | None, out: str) -> str:
    if not pages:
        return pdf
    doc = pymupdf.open(pdf)
    if len(doc) <= pages:
        return pdf
    doc.select(list(range(pages)))
    doc.save(out)
    return out


def make_images(pdf: str, kind: str, out_dir: str) -> list[str]:
    """The page images a recogniser gets: degraded (or scanned) pages after our own preparation."""
    os.makedirs(out_dir, exist_ok=True)
    done = sorted(str(p) for p in Path(out_dir).glob("prepared-*.png"))
    if done:
        return done
    paths = []
    for i, img in enumerate(render(pdf)):
        raw = os.path.join(out_dir, f"page-{i + 1:03d}.{'jpg' if kind == 'photo' else 'png'}")
        cv2.imwrite(raw, img if kind == "real" else degrade(img, kind, seed=1 + i))
        prepared = os.path.join(out_dir, f"prepared-{i + 1:03d}.png")
        prepare_file(raw, prepared, photo=kind == "photo")
        paths.append(prepared)
    return paths


def recognise(engine: str, images: list[str], out_file: str) -> tuple[str | None, float, str]:
    """(path of the MusicXML or None, seconds, error)."""
    if os.path.exists(out_file) and engine != "combined":
        return out_file, 0.0, ""
    start = time.time()
    try:
        if engine == "combined":
            folder = Path(out_file).parent
            read = {e: (folder / f"{e}.musicxml").read_text(encoding="utf-8")
                    for e in ORDER if (folder / f"{e}.musicxml").exists()}
            xml = combine(read).xml
        elif engine == "audiveris-plain":
            xml = clean_omr(run_engine("audiveris", images, on=()))
        elif engine == "audiveris":
            xml = clean_omr(run_engine("audiveris", images))
        else:
            xml = run_engine(engine, images)
    except Exception as exc:  # a recogniser that fails on a page is a result, not a crash
        return None, time.time() - start, str(exc).splitlines()[0][:200]
    Path(out_file).write_text(xml, encoding="utf-8")
    return out_file, time.time() - start, ""


def rhythm_errors(root) -> int:
    """Measures whose voices do not fill the time signature, not counting an upbeat and the short
    last measure that completes it (not every recogniser marks those as such)."""
    problems = find_problems(root)
    lengths = [len(p.findall("measure")) for p in root.findall("part")]
    count = 0
    for p in problems:
        short = all(t < p.expected for t in p.totals.values())
        if short and p.index in (0, lengths[p.part] - 1):
            continue
        count += 1
    return count


def measure(case: dict, kind: str, engine: str, images: list[str], reference: str | None, out_dir: str) -> dict:
    row = {"case": case["name"], "kind": kind, "engine": engine, "pages": len(images)}
    path, seconds, error = recognise(engine, images, os.path.join(out_dir, f"{engine}.musicxml"))
    if seconds:
        row["seconds_per_page"] = round(seconds / max(1, len(images)), 1)
    if not path:
        row["error"] = error
        return row
    try:
        root = read_xml(path)
        row["measures"] = sum(len(p.findall("measure")) for p in root.findall("part"))
        row["flagged_measures"] = rhythm_errors(root)
        found = count_marks(root)
        if reference:
            row.update(score(path, reference))
        else:
            expected = case.get("expected_marks", {})
            row["marks"] = {k: [found[k], expected.get(k)] for k in found}
    except Exception as exc:
        row["error"] = f"Ergebnis nicht lesbar: {str(exc)[:160]}"
    return row


def run(cases: list[dict], out: str, engines: list[str], kinds: list[str], jobs: int) -> list[dict]:
    tasks = []
    for case in cases:
        cdir = os.path.join(out, case["name"])
        os.makedirs(cdir, exist_ok=True)
        pdf = first_pages(case["pdf"], case.get("pages"), os.path.join(cdir, "pages.pdf"))
        digital = pdf_kind(pdf) == "vector"
        reference = case.get("reference")
        if digital and not reference:
            reference = os.path.join(cdir, "reference.musicxml")
            if not os.path.exists(reference):
                Path(reference).write_text(convert(pdf, engine="vector").musicxml, encoding="utf-8")
        for kind in (kinds if digital else ["real"]):
            kdir = os.path.join(cdir, kind)
            images = make_images(pdf, kind, kdir)
            for engine in engines:
                tasks.append((case, kind, engine, images, reference, kdir))
    # "combined" is made from what the single recognisers left behind, so it runs after them
    first = [t for t in tasks if t[2] != "combined"]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        done = dict(zip(map(id, first), pool.map(lambda t: measure(*t), first)))
    return [done[id(t)] if id(t) in done else measure(*t) for t in tasks]


def _pct(x) -> str:
    return "–" if x is None else f"{100 * x:.0f} %"


def summary(rows: list[dict], engines: list[str]) -> str:
    lines = ["# Scan-Testreihe", ""]
    scored = [r for r in rows if "pitch_and_rhythm" in r or ("error" in r and r["kind"] != "real")]
    kinds = sorted({r["kind"] for r in scored})
    if scored:
        lines += ["## Noten richtig (Tonhöhe und Dauer), Mittel über alle Stücke", "",
                  "| Erkenner | " + " | ".join(kinds) + " | Takte mit Rhythmusfehler | Sekunden pro Seite |",
                  "|---|" + "---|" * (len(kinds) + 2)]
        for e in engines:
            cells = []
            for k in kinds:
                rs = [r for r in scored if r["engine"] == e and r["kind"] == k]
                cells.append(_pct(sum(r.get("pitch_and_rhythm", 0.0) for r in rs) / len(rs)) if rs else "–")
            rs = [r for r in scored if r["engine"] == e and "measures" in r]
            flagged = f"{sum(r['flagged_measures'] for r in rs)} von {sum(r['measures'] for r in rs)}" if rs else "–"
            secs = [r["seconds_per_page"] for r in rows if r["engine"] == e and "seconds_per_page" in r]
            lines.append(f"| {e} | " + " | ".join(cells) + f" | {flagged} | "
                         + (f"{sum(secs) / len(secs):.0f}" if secs else "–") + " |")
        lines += ["", "## Zeichen gefunden (Anzahl im Ergebnis / Anzahl in der Lösung, alle Stücke und Arten)", "",
                  "| Erkenner | " + " | ".join(MARKS) + " |", "|---|" + "---|" * len(MARKS)]
        for e in engines:
            rs = [r for r in scored if r["engine"] == e and "marks" in r]
            lines.append(f"| {e} | " + " | ".join(
                f"{sum(r['marks'][m][0] for r in rs)} / {sum(r['marks'][m][1] for r in rs)}" for m in MARKS) + " |")
        lines += ["", "## Einzelergebnisse (Noten richtig)", "",
                  "| Stück | Art | " + " | ".join(engines) + " |", "|---|---|" + "---|" * len(engines)]
        for name in dict.fromkeys(r["case"] for r in scored):
            for k in kinds:
                cells = []
                for e in engines:
                    r = next((r for r in scored if (r["case"], r["kind"], r["engine"]) == (name, k, e)), None)
                    cells.append("–" if r is None else "Fehler" if "error" in r else _pct(r["pitch_and_rhythm"]))
                lines.append(f"| {name} | {k} | " + " | ".join(cells) + " |")
    real = [r for r in rows if r["kind"] == "real"]
    if real:
        lines += ["", "## Echte Scans (keine Lösung: Takte mit Rhythmusfehler und gezählte Zeichen)", "",
                  "| Stück | Erkenner | Takte | Rhythmusfehler | " + " | ".join(MARKS) + " |",
                  "|---|---|---|---|" + "---|" * len(MARKS)]
        for r in real:
            if "error" in r:
                lines.append(f"| {r['case']} | {r['engine']} | Fehler: {r['error']} |" + " |" * (len(MARKS) + 2))
                continue
            marks = " | ".join(
                f"{r['marks'][m][0]}" + (f" / {r['marks'][m][1]}" if r['marks'][m][1] is not None else "")
                for m in MARKS)
            lines.append(f"| {r['case']} | {r['engine']} | {r['measures']} | {r['flagged_measures']} | {marks} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases")
    ap.add_argument("out")
    ap.add_argument("--engines", default="audiveris,homr,copisteria")
    ap.add_argument("--kinds", default="scan,copy,photo")
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    engines = args.engines.split(",")
    rows = run(cases, args.out, engines, args.kinds.split(","), args.jobs)
    Path(args.out, "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    text = summary(rows, engines)
    Path(args.out, "results.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
