"""End-to-end check on a public-domain chorale (Bach BWV 66.6, typeset with MuseScore)."""

import warnings

import music21 as m21
import pytest
from conftest import DATA
from evaluate import compare_parts

from sheet2xml.pipeline import convert, pdf_kind

warnings.filterwarnings("ignore")

PDF = str(DATA / "bach_bwv66_6.pdf")


@pytest.fixture(scope="module")
def result():
    return convert(PDF)


def test_detects_vector_pdf():
    assert pdf_kind(PDF) == "vector"


def test_parts_and_measures(result):
    assert result.engine == "vector"
    assert result.parts == ["Soprano", "Alto", "Tenor", "Bass"]
    assert result.measures == 10
    # pickup and short final measure are fine, nothing else should be flagged
    assert result.flagged_measures == 0


def test_notes_match_reference(result):
    res = m21.converter.parseData(result.musicxml, format="musicxml")
    ref = m21.converter.parse(str(DATA / "bach_bwv66_6.musicxml"))
    for sp, rp in zip(res.parts, ref.parts):
        r = compare_parts(sp, rp)
        assert r["pitch_only"] == 1.0, (sp.partName, r)
        # the reference pads the last measure with a rest that is not printed
        assert r["pitch_and_rhythm"] >= 0.95, (sp.partName, r)


def test_pickup_is_implicit(result):
    assert 'number="0" implicit="yes"' in result.musicxml
