"""Command line: sheet2xml noten.pdf [-o noten.musicxml]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .pipeline import convert


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sheet2xml",
        description="Wandelt Noten (PDF, Foto, Scan) in MusicXML für Sibelius, MuseScore, Dorico und Finale um.",
    )
    ap.add_argument("inputs", nargs="+", help="Ein PDF oder mehrere Bilder (Seiten in Reihenfolge)")
    ap.add_argument("-o", "--output", help="Zieldatei (.musicxml); Standard: neben der Eingabe")
    ap.add_argument("--engine", choices=["auto", "vector", "omr"], default="auto",
                    help="auto: PDFs aus Notenprogrammen direkt lesen, sonst Notenerkennung")
    ap.add_argument("--review", action="store_true",
                    help="Auffällige Takte von Claude prüfen lassen (ANTHROPIC_API_KEY nötig)")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--version", action="version", version=f"sheet2xml {__version__}")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    def say(msg: str) -> None:
        if not args.quiet:
            print(f"• {msg}", file=sys.stderr)

    res = convert(args.inputs, engine=args.engine, review=args.review, progress=say)
    out = Path(args.output) if args.output else Path(args.inputs[0]).with_suffix(".musicxml")
    out.write_text(res.musicxml, encoding="utf-8")
    if not args.quiet:
        info = f"{res.engine}"
        if res.parts:
            info += f", {len(res.parts)} Stimme(n), {res.measures} Takte"
        if res.flagged_measures:
            info += f", {res.flagged_measures} Takte mit Rhythmus-Warnung"
        print(f"✓ {out} ({info})", file=sys.stderr)
        for w in res.warnings:
            print(f"  Hinweis: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
