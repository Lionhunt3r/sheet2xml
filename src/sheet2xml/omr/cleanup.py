"""Repair what the recognisers get wrong in a known way.

Audiveris reads into the lyrics of an instrumental part what is not sung: dynamics set below
the staff (p, mf) and the copyright lines at the foot of the page.
homr writes every tremolo as one half of a tremolo between two notes, and its elements stand
in an order that stricter notation programs refuse."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

DYNAMICS = {"p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sf", "sfz", "fp", "fz", "rf", "rfz"}
FOOTER = re.compile(r"©|copyright|licen[cs]e|rights|reserved|permission|photocopy|printed in|publishing", re.I)
MIN_LYRICS = 12  # fewer real syllables than this in a part: reading noise, not a sung text


def clean_omr(xml: str) -> str:
    root = ET.fromstring(xml.encode("utf-8"))
    for part in root.iter("part"):
        measures = part.findall("measure")
        found = []  # (measure index, note, lyric, text)
        for mi, m in enumerate(measures):
            for note in m.findall("note"):
                for lyr in note.findall("lyric"):
                    found.append((mi, m, note, lyr, (lyr.findtext("text") or "").strip()))
        footer = {mi for mi, _, _, _, t in found if FOOTER.search(t)}
        near_footer = {mi + d for mi in footer for d in (-1, 0, 1)}
        keep = []
        for mi, m, note, lyr, text in found:
            word = text.lower().strip(".")
            if word in DYNAMICS and mi not in near_footer:
                _add_dynamic(m, note, word)
            elif mi in near_footer or not any(ch.isalpha() for ch in text):
                pass
            else:
                keep.append((note, lyr))
                continue
            note.remove(lyr)
        if len(keep) < MIN_LYRICS:
            for note, lyr in keep:
                note.remove(lyr)
    header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    return header + ET.tostring(root, encoding="unicode")


def _add_dynamic(measure: ET.Element, note: ET.Element, word: str) -> None:
    d = ET.Element("direction", placement="below")
    ET.SubElement(ET.SubElement(ET.SubElement(d, "direction-type"), "dynamics"), word)
    staff = note.find("staff")
    if staff is not None:
        ET.SubElement(d, "staff").text = staff.text
    measure.insert(list(measure).index(note), d)


# the order MusicXML prescribes inside these elements
ORDER = {
    "note": ["grace", "cue", "chord", "pitch", "unpitched", "rest", "duration", "tie", "instrument", "footnote",
             "level", "voice", "type", "dot", "accidental", "time-modification", "stem", "notehead", "notehead-text",
             "staff", "beam", "notations", "lyric", "play", "listen"],
    "attributes": ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol", "instruments", "clef",
                   "staff-details", "transpose", "for-part", "directive", "measure-style"],
    "direction": ["direction-type", "offset", "footnote", "level", "voice", "staff", "sound", "listening"],
}


def put_in_order(root: ET.Element) -> None:
    """Sort the children of notes, attributes and directions the way the format prescribes."""
    for el in root.iter():
        order = ORDER.get(el.tag)
        if order is None or len(el) < 2:
            continue
        rank = {tag: i for i, tag in enumerate(order)}
        children = sorted(el, key=lambda c: rank.get(c.tag, len(order)))  # stable: equal tags keep their order
        if children != list(el):
            el[:] = children


def clean_homr(xml: str) -> str:
    """homr marks tremolo notes alternately as start and end of a tremolo between two notes,
    whatever is printed. Strokes through one stem (the common case, bowed tremolo) is what we write."""
    root = ET.fromstring(xml.encode("utf-8"))
    for t in root.iter("tremolo"):
        t.set("type", "single")
    header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    return header + ET.tostring(root, encoding="unicode")
