"""Guess the written-to-sounding transposition from an instrument name."""

from __future__ import annotations

import re

# (pattern on the normalised name, (diatonic, chromatic)) - first match wins
_RULES: list[tuple[str, tuple[int, int]]] = [
    (r"(tenor ?sax|tenorsax)", (-8, -14)),
    (r"(bariton ?sax|baritone ?sax|baritonsax)", (-12, -21)),
    (r"(alt ?sax|alto ?sax|altsax)", (-5, -9)),
    (r"(sopran ?sax|soprano ?sax|sopransax)", (-1, -2)),
    (r"(bass ?klar|bass ?clar|bassklar)", (-8, -14)),
    (r"(klar|clar|cl\b|kl\b).*\bin a\b|\bin a\b.*(klar|clar)", (-2, -3)),
    (r"(klar|clar).*\b(in )?(es|eb|e flat)\b", (2, 3)),
    (r"(horn|corn|cor\b|hn\b).*\bin e\b", (-5, -8)),
    (r"(horn|corni|corno|cor\b|hn\b|horns)", (-4, -7)),
    (r"(englisch|english|cor anglais|corno ingl)", (-4, -7)),
    (r"\bin (b|bb|b flat|si b|sib)\b", (-1, -2)),
    (r"(klarinette|clarinet|clarinetto|clarinetti|trompete|trumpet|tromba|trombe|flugelhorn|flugelhorn)", (-1, -2)),
    (r"\bin (es|eb|e flat|mib)\b", (-5, -9)),
    (r"\bin f\b", (-4, -7)),
    (r"(kontrabass|contrabass|double bass|string bass|sting bass|contrabassi|\bkb\b|\bcb\b)", (-7, -12)),
    (r"(gitarre|guitar|chitarra)", (-7, -12)),
    (r"(piccolo|pikkolo|ottavino)", (7, 12)),
    (r"(glockenspiel|glsp)", (14, 24)),
    (r"(xylophon|xylophone)", (7, 12)),
]


def _normalise(name: str) -> str:
    n = name.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("♭", "b"), ("ß", "ss")):
        n = n.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", " ", n)


def transposition_for(name: str) -> tuple[int, int] | None:
    n = _normalise(name)
    if not n.strip():
        return None
    if re.search(r"\bin c\b", n):
        return None
    for pat, tr in _RULES:
        if re.search(pat, n):
            return tr
    return None
