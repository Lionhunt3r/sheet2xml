"""Join several MusicXML documents (e.g. movements exported separately) part by part."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def merge_musicxml(texts: list[str]) -> str:
    if len(texts) == 1:
        return texts[0]
    docs = [ET.fromstring(t.encode("utf-8")) for t in texts]
    base = docs[0]
    base_parts = base.findall("part")
    for doc in docs[1:]:
        for i, part in enumerate(doc.findall("part")):
            if i >= len(base_parts):
                break
            target = base_parts[i]
            last = target.findall("measure")
            n = len(last)
            for m in part.findall("measure"):
                n += 1
                m.set("number", str(n))
                target.append(m)
    header = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    return header + ET.tostring(base, encoding="unicode")
