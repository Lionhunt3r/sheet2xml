"""The recognisers' command lines and the tools that measure them."""

import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from sheet2xml.omr import audiveris, engines

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

DATA = os.path.join(os.path.dirname(__file__), "data")


def test_audiveris_switches_on_by_default(monkeypatch):
    monkeypatch.delenv("AUDIVERIS_SWITCHES", raising=False)
    cmd = audiveris.command("Audiveris", "/out", ["book.pdf"])
    for name in ("tremolos", "implicitTuplets", "chordNames"):
        assert f"org.audiveris.omr.sheet.ProcessingSwitches.{name}=true" in cmd
    assert cmd[-2:] == ["--", "book.pdf"]


def test_audiveris_switches_from_environment(monkeypatch):
    monkeypatch.setenv("AUDIVERIS_SWITCHES", "smallHeads, tremolos")
    cmd = audiveris.command("Audiveris", "/out", ["book.pdf"])
    assert "org.audiveris.omr.sheet.ProcessingSwitches.smallHeads=true" in cmd
    assert not any("implicitTuplets" in c for c in cmd)
    monkeypatch.setenv("AUDIVERIS_SWITCHES", "")
    assert not any("ProcessingSwitches" in c for c in audiveris.command("Audiveris", "/out", ["book.pdf"]))


def test_explicit_switches_win_over_environment(monkeypatch):
    monkeypatch.setenv("AUDIVERIS_SWITCHES", "tremolos")
    assert not any("ProcessingSwitches" in c for c in audiveris.command("Audiveris", "/out", ["b.pdf"], on=()))


def test_engines_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", str(tmp_path / "nope"))
    monkeypatch.setenv("COPISTERIA_HOME", str(tmp_path / "nope"))
    found = engines.available_engines()
    assert "homr" not in found and "copisteria" not in found
    with pytest.raises(audiveris.OMRError):
        engines.run_homr(["page.png"])
    with pytest.raises(audiveris.OMRError):
        engines.run_copisteria(["page.png"])
    with pytest.raises(audiveris.OMRError):
        engines.run_engine("unbekannt", ["page.png"])


def test_engines_found(monkeypatch, tmp_path):
    homr = tmp_path / "homr"
    homr.write_text("")
    home = tmp_path / "copisteria"
    (home / "models").mkdir(parents=True)
    python = tmp_path / "python"
    python.write_text("")
    monkeypatch.setenv("HOMR_BIN", str(homr))
    monkeypatch.setenv("COPISTERIA_HOME", str(home))
    monkeypatch.setenv("COPISTERIA_PYTHON", str(python))
    found = engines.available_engines()
    assert "homr" in found and "copisteria" in found


def test_degrade_kinds():
    from degrade import KINDS, degrade, render

    page = render(os.path.join(DATA, "bach_bwv66_6.pdf"), dpi=100)[0]
    for kind in KINDS:
        out = degrade(page, kind, seed=3)
        assert out.dtype == np.uint8 and out.ndim == 2
        assert np.array_equal(out, degrade(page, kind, seed=3)), "same seed, same page"
        dark = (out < 128).mean()
        assert 0.005 < dark < 0.6, f"{kind}: the notes must survive"
    assert set(np.unique(degrade(page, "copy", seed=3))) <= {0, 255}


def test_count_marks():
    from evaluate import count_marks

    root = ET.fromstring("""<score-partwise><part id="P1"><measure number="1">
      <direction><direction-type><dynamics><p/></dynamics></direction-type></direction>
      <direction><direction-type><wedge type="crescendo"/></direction-type></direction>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><tie type="start"/>
        <notations><slur type="start"/><ornaments><tremolo type="single">3</tremolo></ornaments>
        <articulations><staccato/><accent/></articulations></notations>
        <lyric><text>la</text></lyric></note>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><tie type="stop"/>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
        <notations><slur type="stop"/></notations></note>
      <direction><direction-type><wedge type="stop"/></direction-type></direction>
    </measure></part></score-partwise>""")
    marks = count_marks(root)
    assert marks["dynamics"] == 1 and marks["hairpins"] == 1 and marks["slurs"] == 1
    assert marks["ties"] == 1 and marks["tremolos"] == 1 and marks["articulations"] == 2
    assert marks["tuplet_notes"] == 1 and marks["lyrics"] == 1


def test_score_of_the_reference_itself():
    from evaluate import score

    ref = os.path.join(DATA, "bach_bwv66_6.musicxml")
    s = score(ref, ref)
    assert s["pitch_and_rhythm"] == 1.0 and s["pitch_only"] == 1.0
    assert s["parts_result"] == s["parts_reference"] == 4
    assert all(found == expected for found, expected in s["marks"].values())
