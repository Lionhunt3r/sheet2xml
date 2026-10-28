import zipfile

from PIL import Image

from sheet2xml.omr.review import find_problems, read_layout, review_omr
from sheet2xml.review import MeasureFix, NoteOut

SHEET = """<sheet><scale><interline main="20"/></scale><page id="1" measure-count="2"><system id="1">
<stack id="1" left="100" right="400"/><stack id="2" left="400" right="700"/>
<part id="1"><staff id="1"><lines>
<line><point x="100" y="100"/><point x="700" y="100"/></line>
<line><point x="100" y="180"/><point x="700" y="180"/></line>
</lines></staff></part></system></page></sheet>"""

XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
<part id="P1">
<measure number="1"><attributes><divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>6</duration><voice>1</voice><type>half</type><dot/></note>
</measure>
<measure number="2">
<note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>quarter</type>
<lyric number="1"><syllabic>single</syllabic><text>Tag</text></lyric></note>
<barline location="right"><bar-style>light-heavy</bar-style></barline>
</measure>
</part></score-partwise>"""


class _Resp:
    stop_reason = "end_turn"

    def __init__(self, fix):
        self.parsed_output = fix


class _Client:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def parse(self, **kw):
        self.calls += 1
        notes = [NoteOut(rest=False, pitches=["D5"], duration="quarter", dots=0, tuplet="triplet", voice=1, staff=1,
                         whole_measure_rest=False) for _ in range(3)]
        notes.append(NoteOut(rest=True, pitches=[], duration="quarter", dots=0, tuplet="none", voice=1, staff=1,
                             whole_measure_rest=False))
        return _Resp(MeasureFix(reading_is_correct=False, notes=notes, comment=""))


def test_omr_review_replaces_short_measure(tmp_path):
    omr = tmp_path / "book.omr"
    with zipfile.ZipFile(omr, "w") as z:
        z.writestr("sheet#1/sheet#1.xml", SHEET)
    img = tmp_path / "page.png"
    Image.new("L", (800, 400), 255).save(img)
    assert len(read_layout(str(omr))) == 2

    import xml.etree.ElementTree as ET

    probs = find_problems(ET.fromstring(XML.split("\n", 1)[1].encode()))
    assert [p.index for p in probs] == [1]

    client = _Client()
    out, fixed = review_omr(XML, str(omr), [str(img)], client=client)
    assert fixed == 1 and client.calls == 1
    root = ET.fromstring(out.split("\n", 1)[1].encode())
    assert root.findtext("part/measure/attributes/divisions") == "6"  # grown for the triplets
    m2 = root.findall("part/measure")[1]
    notes = m2.findall("note")
    assert [n.findtext("duration") for n in notes] == ["4", "4", "4", "6"]
    assert notes[0].findtext("lyric/text") == "Tag"  # the lyric read before is kept
    assert list(m2)[-1].tag == "barline"
    assert not find_problems(root)
