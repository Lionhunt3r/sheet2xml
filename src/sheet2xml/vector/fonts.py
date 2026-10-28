"""Glyph tables for the music fonts found in PDFs exported by notation programs.

Two families matter:

* Sonata-compatible fonts (Sibelius: Opus, Helsinki, Inkpen, Reprise; Finale:
  Maestro, Petrucci, Jazz, Engraver; Score: Sonata itself). They place symbols
  on ASCII/MacRoman positions, e.g. a black notehead is "œ".
* SMuFL fonts (Dorico: Bravura, Petaluma; MuseScore: Leland, Bravura, MScore,
  Emmentaler; Finale 27+: Finale Maestro, Finale Broadway). They use the
  Private Use Area starting at U+E000.

Every glyph is mapped to a short semantic name. Names are grouped by prefix:
``clef.*``, ``head.*``, ``acc.*``, ``rest.*``, ``flag.*``, ``dot``,
``time.*``, ``dyn.*``, ``art.*``, ``brace``, ``tuplet.*``.
"""

from __future__ import annotations

import re

# --- Sonata-compatible mapping (characters as Unicode after MacRoman decode) ---
SONATA: dict[str, str] = {
    "&": "clef.G",
    "V": "clef.G8vb",
    "?": "clef.F",
    "t": "clef.F",  # small/alternative F clef in some fonts
    "B": "clef.C",
    "/": "clef.perc",
    "œ": "head.black",
    "˙": "head.half",
    "w": "head.whole",
    "W": "head.breve",
    "#": "acc.sharp",
    "b": "acc.flat",
    "n": "acc.natural",
    "Ü": "acc.dsharp",
    "º": "acc.dflat",
    "∑": "rest.whole",
    "Ó": "rest.half",
    "Œ": "rest.quarter",
    "‰": "rest.8",
    "≈": "rest.16",
    "®": "rest.32",
    "Ù": "rest.64",
    "j": "flag.up.1",
    "J": "flag.down.1",
    "r": "flag.up.2",
    "R": "flag.down.2",
    "™": "dot",  # Sibelius (OpusSpecial) augmentation dot
    ".": "dot",
    "k": "dot",
    "c": "time.common",
    "C": "time.cut",
    ">": "art.accent",
    "-": "art.tenuto",
    "U": "art.fermata",
    "u": "art.fermata",
    "{": "brace",
    "Ÿ": "orn.trill",
}
for _d in "0123456789":
    SONATA[_d] = f"time.{_d}"

# Dynamics as they appear in Sonata music fonts or in their text companions
# (OpusText, MaestroText...).
SONATA_DYN: dict[str, str] = {
    "p": "p", "f": "f", "m": "m", "s": "s", "z": "z", "r": "r", "n": "n",
    "P": "mp", "F": "mf", "π": "pp", "ƒ": "ff", "∏": "ppp", "Ï": "fff",
    "S": "sf", "Z": "sfz",
}

# --- SMuFL mapping (code point -> name) ---
SMUFL: dict[int, str] = {
    0xE050: "clef.G", 0xE052: "clef.G8vb", 0xE053: "clef.G8va",
    0xE07A: "clef.G", 0xE062: "clef.F", 0xE064: "clef.F8vb", 0xE07C: "clef.F",
    0xE05C: "clef.C", 0xE07B: "clef.C", 0xE069: "clef.perc", 0xE06A: "clef.perc",
    0xE0A0: "head.breve", 0xE0A2: "head.whole", 0xE0A3: "head.half", 0xE0A4: "head.black",
    0xE0A9: "head.black", 0xE0A8: "head.whole", 0xE0FA: "head.black", 0xE0D9: "head.half",
    0xE0DB: "head.black",
    # Dorico/Bravura "large" or engraving alternates
    0xF4BE: "head.black", 0xF4BD: "head.half", 0xF4BC: "head.whole",
    0xE260: "acc.flat", 0xE261: "acc.natural", 0xE262: "acc.sharp",
    0xE263: "acc.dsharp", 0xE264: "acc.dflat",
    0xE4E2: "rest.breve", 0xE4E3: "rest.whole", 0xE4E4: "rest.half", 0xE4E5: "rest.quarter",
    0xE4E6: "rest.8", 0xE4E7: "rest.16", 0xE4E8: "rest.32", 0xE4E9: "rest.64",
    0xE4EE: "rest.hbar",
    0xE240: "flag.up.1", 0xE241: "flag.down.1", 0xE242: "flag.up.2", 0xE243: "flag.down.2",
    0xE244: "flag.up.3", 0xE245: "flag.down.3", 0xE246: "flag.up.4", 0xE247: "flag.down.4",
    0xE1E7: "dot",
    0xE08A: "time.common", 0xE08B: "time.cut",
    0xE4A0: "art.accent", 0xE4A1: "art.accent", 0xE4A2: "art.staccato", 0xE4A3: "art.staccato",
    0xE4A4: "art.tenuto", 0xE4A5: "art.tenuto", 0xE4C0: "art.fermata", 0xE4C1: "art.fermata",
    0xE000: "brace", 0xE566: "orn.trill",
    0xE520: "dyn.p", 0xE521: "dyn.m", 0xE522: "dyn.f", 0xE523: "dyn.r", 0xE524: "dyn.s",
    0xE525: "dyn.z", 0xE526: "dyn.n",
    0xE52B: "dyn.ppp", 0xE52A: "dyn.ppp", 0xE52C: "dyn.mp", 0xE52D: "dyn.mf",
    0xE52F: "dyn.ff", 0xE530: "dyn.fff", 0xE536: "dyn.sf", 0xE539: "dyn.sfz",
}
for _i in range(10):
    SMUFL[0xE080 + _i] = f"time.{_i}"
    SMUFL[0xE880 + _i] = f"tuplet.{_i}"

# Fonts that carry music symbols in each family. Matching is by substring on
# the font name with any subset prefix ("ABCDEF+") removed.
SONATA_FONTS = re.compile(
    r"^(Opus|Helsinki|Inkpen|Reprise|Norfolk|Maestro|Petrucci|Jazz|Engraver(?!Text)|"
    r"Sonata|Tamburo|Broadway|Golden|Pmusic|Megadeth)",
    re.I,
)
SMUFL_FONTS = re.compile(
    r"^(Bravura|Petaluma|Leland|MScore|Emmentaler|Gonville|Sebastian|Finale ?Maestro|"
    r"FinaleMaestro|Finale ?Broadway|FinaleBroadway|Finale ?Jazz|Ekmelos|Leipzig|MuseJazz)",
    re.I,
)
# Companion text fonts that carry dynamics letters.
DYN_TEXT_FONTS = re.compile(r"(Opus|Helsinki|Inkpen|Reprise|Maestro|Sonata).*Text", re.I)
# Special/extra symbol fonts that share Sonata codes only partially.
SPECIAL_FONTS = re.compile(r"Special", re.I)


def clean_font_name(name: str) -> str:
    return name.split("+", 1)[-1]


def font_family(name: str) -> str | None:
    """Return 'sonata', 'smufl', 'dyn' or None for a (cleaned) font name."""
    n = clean_font_name(name)
    if SMUFL_FONTS.search(n):
        return "smufl" if not n.lower().endswith("text") else "smufl-text"
    if DYN_TEXT_FONTS.search(n):
        return "dyn"
    if SONATA_FONTS.search(n):
        return "sonata-special" if SPECIAL_FONTS.search(n) else "sonata"
    return None


def normalize_char(ch: str) -> str:
    """Undo symbol-font encodings: U+F020..U+F0FF carry raw MacRoman bytes."""
    cp = ord(ch)
    if 0xF020 <= cp <= 0xF0FF:
        return bytes([cp - 0xF000]).decode("mac_roman")
    return ch


# Sibelius' "OpusSpecial" fonts reuse a few code positions differently.
SONATA_SPECIAL: dict[str, str] = {
    "™": "dot",
    "{": "brace",
}


def glyph_name(font: str, ch: str) -> str | None:
    fam = font_family(font)
    if fam is None:
        return None
    if fam.startswith("smufl"):
        return SMUFL.get(ord(ch))
    c = normalize_char(ch)
    if fam == "dyn":
        if c.isdigit():
            return f"tuplet.{c}"
        d = SONATA_DYN.get(c)
        return f"dyn.{d}" if d else None
    if fam == "sonata-special":
        return SONATA_SPECIAL.get(c)
    return SONATA.get(c)


def is_music_font(font: str) -> bool:
    fam = font_family(font)
    return fam in ("sonata", "sonata-special", "smufl")
