"""Repair what Audiveris reads into the lyrics of an instrumental part: dynamics set below
the staff (p, mf) and the copyright lines at the foot of the page."""

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
