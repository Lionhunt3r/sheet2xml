"""One score out of the readings of several recognisers."""

import xml.etree.ElementTree as ET
from fractions import Fraction

from sheet2xml.omr.combine import align, combine, read_part


def note(step, octave, dur, extra=""):
    return (f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
            f"<duration>{dur}</duration><voice>1</voice>{extra}</note>")


def rest(dur):
    return f'<note><rest measure="yes"/><duration>{dur}</duration><voice>1</voice></note>'


def score(measures, divisions=1, beats=4):
    """measures: list of strings (the content of each measure)."""
    out = []
    for i, content in enumerate(measures):
        att = (f"<attributes><divisions>{divisions}</divisions><time><beats>{beats}</beats>"
               f"<beat-type>4</beat-type></time></attributes>") if i == 0 else ""
        out.append(f'<measure number="{i + 1}">{att}{content}</measure>')
    return ('<?xml version="1.0"?><score-partwise><part-list><score-part id="P1"><part-name>x</part-name>'
            '</score-part></part-list><part id="P1">' + "".join(out) + "</part></score-partwise>")


def four(steps, dur=1, octave=4):
    return "".join(note(s, octave, dur) for s in steps)


def pitches(xml, measure):
    part = ET.fromstring(xml.split("?>", 1)[1]).find("part")
    return [p for _, _, ps in read_part(part)[measure].notes for p in ps]


def test_agreed_outvoted_disputed():
    homr = score([four("CDEF"), four("GGGG"), four("CCCC")])
    cop = score([four("CDEF"), four("GABC"), four("DDDD")], divisions=2).replace(
        "<duration>1</duration>", "<duration>2</duration>")
    aud = score([four("CDEE"), four("GABC"), four("EEEE")])
    c = combine({"audiveris": aud, "homr": homr, "copisteria": cop})
    assert c.base == "homr" and c.engines == ["homr", "copisteria", "audiveris"]
    assert [v.state for v in c.verdicts] == ["agreed", "outvoted", "disputed"]
    assert c.verdicts[1].chosen == "copisteria" and [v.number for v in c.disputed] == ["3"]
    assert pitches(c.xml, 0) == ["C4", "D4", "E4", "F4"]
    assert pitches(c.xml, 1) == ["G4", "A4", "B4", "C4"], "the two that agree win"
    assert pitches(c.xml, 2) == ["C4", "C4", "C4", "C4"], "nobody agrees: the base stands"
    part = ET.fromstring(c.xml.split("?>", 1)[1]).find("part")
    assert all(r.fills for r in read_part(part)), "notes moved between different divisions keep their length"


def test_single_recogniser_and_unreadable_one():
    c = combine({"audiveris": score([four("CDEF")]), "homr": "<broken"})
    assert c.base == "audiveris" and [v.state for v in c.verdicts] == ["single"]
    assert any("homr" in n for n in c.notes)


def test_other_part_count_is_left_out():
    two = score([four("CDEF")]).replace("</part>", '</part><part id="P2"><measure number="1"/></part>')
    c = combine({"homr": score([four("CDEF")]), "audiveris": two})
    assert c.engines == ["homr"] and any("audiveris" in n for n in c.notes)


def test_rhythm_that_adds_up_wins_a_dispute():
    homr = score([four("CDEF"), four("GAB"), four("CCCC")])  # one beat short in measure 2
    cop = score([four("CDEF"), four("GABD"), four("CCCC")])
    aud = score([four("CDEF"), four("GABE") + note("F", 4, 1), four("CCCC")])
    c = combine({"homr": homr, "copisteria": cop, "audiveris": aud})
    v = c.verdicts[1]
    assert v.state == "disputed" and v.chosen == "copisteria"
    assert pitches(c.xml, 1) == ["G4", "A4", "B4", "D4"]


def test_signs_the_base_lacks_are_lent():
    wedge = '<direction placement="below"><direction-type><wedge type="{}"/></direction-type></direction>'
    dyn = "<direction><direction-type><dynamics><f/></dynamics></direction-type></direction>"
    homr = score([dyn + four("CDEF"), four("GGGG")])
    cop = score([note("C", 4, 1) + wedge.format("crescendo") + four("DEF") + wedge.format("stop"),
                 four("GGGG") + wedge.format("diminuendo")])  # the last hairpin never ends
    lyric = "<lyric><text>la</text></lyric>"
    aud = score([note("C", 4, 1, lyric) + four("DEF"),
                 '<harmony><root><root-step>G</root-step></root><kind>major</kind></harmony>' + four("GGGG")])
    c = combine({"homr": homr, "copisteria": cop, "audiveris": aud})
    assert c.borrowed == {"Gabeln": "copisteria", "Akkordsymbole": "audiveris", "Liedtext": "audiveris"}
    part = ET.fromstring(c.xml.split("?>", 1)[1]).find("part")
    m1, m2 = part.findall("measure")
    tags = [("wedge:" + el.find("direction-type/wedge").get("type")) if el.find("direction-type/wedge") is not None
            else el.tag for el in m1]
    assert tags == ["attributes", "direction", "note", "wedge:crescendo", "note", "note", "note", "wedge:stop"]
    assert m1.find("direction/direction-type/dynamics/f") is not None, "the base keeps its own dynamics"
    assert m1.find("note/lyric/text").text == "la"
    assert m2.find("harmony") is not None and list(m2)[0].tag == "harmony"
    assert m2.find("direction") is None, "half a hairpin is dropped"


def test_ottava_line_moves_the_notes_of_a_recogniser_that_does_not_know_it():
    shift = '<direction><direction-type><octave-shift type="{}" size="8"/></direction-type></direction>'
    homr = score([four("CDEF"), four("GABC"), four("CDEF")])
    cop = score([four("CDEF"), shift.format("down") + four("GABC", octave=5) + shift.format("stop"), four("CDEF")])
    c = combine({"homr": homr, "copisteria": cop})
    assert c.borrowed.get("Oktavierungen") == "copisteria"
    assert pitches(c.xml, 1) == ["G5", "A5", "B5", "C5"] and pitches(c.xml, 2) == ["C4", "D4", "E4", "F4"]
    assert [v.state for v in c.verdicts] == ["agreed"] * 3
    assert c.xml.count("octave-shift") == 2
    # a line the other recogniser only imagined changes nothing
    cop = score([four("CDEF"), shift.format("down") + four("GABC") + shift.format("stop"), four("CDEF")])
    c = combine({"homr": homr, "copisteria": cop})
    assert "Oktavierungen" not in c.borrowed and pitches(c.xml, 1) == ["G4", "A4", "B4", "C4"]


def test_long_rest_takes_the_larger_count():
    def long_rest(n):
        style = f"<attributes><measure-style><multiple-rest>{n}</multiple-rest></measure-style></attributes>"
        return [style + rest(4)] + [rest(4)] * (n - 1)

    homr = score([four("CDEF"), *long_rest(4), four("GGGG")])
    aud = score([four("CDEF"), *long_rest(6), four("GGGG")])
    c = combine({"homr": homr, "audiveris": aud})
    part = ET.fromstring(c.xml.split("?>", 1)[1]).find("part")
    measures = part.findall("measure")
    assert len(measures) == 8 and [m.get("number") for m in measures] == [str(i) for i in range(1, 9)]
    assert measures[1].findtext("attributes/measure-style/multiple-rest") == "6"
    assert pitches(c.xml, 7) == ["G4"] * 4
    assert all(v.state == "agreed" for v in c.verdicts) and len(c.verdicts) == 8


def test_align_finds_a_missing_measure():
    a = read_part(ET.fromstring(score([four("CDEF"), four("GABC"), four("DDDD"), four("EEEE")]).split("?>", 1)[1])
                  .find("part"))
    b = read_part(ET.fromstring(score([four("CDEF"), four("DDDD"), four("EEEE")]).split("?>", 1)[1]).find("part"))
    assert align(a, b) == [(0, 0), (1, None), (2, 1), (3, 2)]
    assert a[0].fills and a[0].totals == (Fraction(4),)


def test_recognize_combines_what_is_installed(monkeypatch, tmp_path):
    from PIL import Image

    from sheet2xml import omr

    page = tmp_path / "seite.png"
    Image.new("L", (600, 400), 255).save(page)
    answers = {"homr": score([four("CDEF"), four("GGGG")]), "copisteria": score([four("CDEF"), four("GGGA")])}

    def fake(name, images, **kw):
        assert len(images) == 1
        if name == "copisteria":
            raise omr.OMRError("kaputt")
        return answers[name]

    monkeypatch.setattr(omr, "available_engines", lambda: ["homr", "copisteria"])
    monkeypatch.setattr(omr, "run_engine", fake)
    said = []
    rec = omr.recognize([str(page)], preprocess=False, progress=said.append)
    assert rec.engines == ["homr"] and rec.base == "homr" and rec.disputed == []
    assert any("copisteria ist ausgefallen" in n for n in rec.notes)

    monkeypatch.setattr(omr, "run_engine", lambda name, images, **kw: answers[name])
    rec = omr.recognize([str(page)], preprocess=False, progress=said.append)
    assert rec.engines == ["homr", "copisteria"] and rec.disputed == ["2"]
    assert any("strittig" in s for s in said)

    monkeypatch.setenv("SHEET2XML_ENGINES", "copisteria")
    assert omr.chosen_engines() == ["copisteria"]
    monkeypatch.setattr(omr, "available_engines", lambda: [])
    assert not omr.available()


def test_ties_seen_by_a_recogniser_that_agrees_are_taken():
    tie = '<tie type="{0}"/><notations><tied type="{0}"/></notations>'
    homr = score([four("CDE") + note("F", 4, 1, tie.format("start")), four("FGAB"), four("CCCC")])
    cop = score([four("CDE") + note("F", 4, 1, tie.format("start")),
                 note("F", 4, 1, tie.format("stop")) + four("GA") + note("B", 4, 1, tie.format("start")),
                 note("C", 4, 1, tie.format("stop")) + four("CCC")])
    c = combine({"homr": homr, "copisteria": cop})
    part = ET.fromstring(c.xml.split("?>", 1)[1]).find("part")
    ties = [[t.get("type") for t in n.findall("tie")] for n in part.iter("note")]
    assert ties[3] == ["start"] and ties[4] == ["stop"], "the missing end of the base's own tie"
    assert ties[7] == [] and ties[8] == [], "B to C is not a tie, whatever one recogniser says"
    assert all(len(n.findall("tie")) == len(n.findall("notations/tied")) for n in part.iter("note"))


def test_upbeat_is_recognised_and_not_reported_as_an_error():
    from sheet2xml.omr.review import find_problems

    homr = score([note("G", 4, 1), four("CDEF"), four("GAB")])
    cop = score([note("G", 4, 1), four("CDEF"), four("GAB")])
    c = combine({"homr": homr, "copisteria": cop})
    root = ET.fromstring(c.xml.split("?>", 1)[1])
    assert root.find("part/measure").get("implicit") == "yes"
    assert find_problems(root) == []
    # a short first measure that nothing completes is a reading error, not an upbeat
    c = combine({"homr": score([note("G", 4, 1), four("CDEF"), four("GABC")]), "copisteria": cop})
    root = ET.fromstring(c.xml.split("?>", 1)[1])
    assert root.find("part/measure").get("implicit") is None and len(find_problems(root)) == 1
