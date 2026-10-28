from fractions import Fraction

import numpy as np
import cv2

from sheet2xml.model import Event, Measure, Part, Pitch, Score
from sheet2xml.musicxml import to_musicxml
from sheet2xml.omr.merge import merge_musicxml
from sheet2xml.omr.preprocess import interline, prepare
from sheet2xml.transpose import transposition_for


def test_transpositions():
    assert transposition_for("Klarinette in B") == (-1, -2)
    assert transposition_for("Horn in F") == (-4, -7)
    assert transposition_for("Violine I") is None


def _score() -> Score:
    m = Measure(number=1, time=(3, 4), key=-1, expected=Fraction(3))
    m.events = [
        Event(x=0, duration=Fraction(2), pitches=[Pitch("B", 4, -1)], base=Fraction(2)),
        Event(x=1, duration=Fraction(1), pitches=[], base=Fraction(1)),
    ]
    return Score(title="Test", parts=[Part(name="Flöte", measures=[m])])


def test_musicxml_writer():
    xml = to_musicxml(_score())
    assert "<fifths>-1</fifths>" in xml
    assert "<beats>3</beats>" in xml
    assert "<alter>-1</alter>" in xml
    assert "<rest" in xml
    assert "<part-name>Flöte</part-name>" in xml


def test_merge_keeps_measures():
    xml = to_musicxml(_score())
    merged = merge_musicxml([xml, xml])
    assert merged.count("<measure ") == 2


def _staff_image(spacing: int) -> np.ndarray:
    img = np.full((800, 1200), 255, np.uint8)
    for top in (100, 400):
        for i in range(5):
            y = top + i * spacing
            cv2.line(img, (50, y), (1150, y), 0, 2)
    return img


def test_interline_and_rescale():
    img = _staff_image(12)
    assert abs(interline(img) - 12) <= 2
    out = prepare(img, photo=False)
    assert abs(interline(out) - 22) <= 3
