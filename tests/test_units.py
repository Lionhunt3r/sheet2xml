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


def test_tuplet_with_dotted_pair_is_one_group():
    # dotted eighth, sixteenth, eighth under one "3"
    m = Measure(number=1, time=(1, 4), expected=Fraction(1))
    t = (3, 2)
    m.events = [
        Event(x=0, duration=Fraction(1, 2), pitches=[Pitch("C", 5)], base=Fraction(1, 2), dots=1, tuplet=t),
        Event(x=1, duration=Fraction(1, 6), pitches=[Pitch("D", 5)], base=Fraction(1, 4), tuplet=t),
        Event(x=2, duration=Fraction(1, 3), pitches=[Pitch("E", 5)], base=Fraction(1, 2), tuplet=t),
    ]
    xml = to_musicxml(Score(title="T", parts=[Part(name="Fl", measures=[m])]))
    assert xml.count('<tuplet type="start"') == 1
    assert xml.count('<tuplet type="stop"') == 1


def test_hidden_rests_complete_a_second_voice():
    from sheet2xml.vector.recognize import SystemReader

    ev = [Event(x=float(i * 10), duration=Fraction(1), pitches=[Pitch("C", 5)], voice=1) for i in range(3)]
    ev.append(Event(x=10.0, duration=Fraction(1), pitches=[Pitch("A", 4)], voice=2))
    SystemReader._fill_hidden_rests(None, ev, Fraction(3), 2.0)
    v2 = sorted((e for e in ev if e.voice == 2), key=lambda e: e.x)
    assert [e.hidden for e in v2] == [True, False, True]
    assert sum(e.duration for e in v2) == 3


def test_lyrics_syllables():
    m = Measure(number=1, time=(2, 4), expected=Fraction(2))
    m.events = [
        Event(x=0, duration=Fraction(1), pitches=[Pitch("C", 5)], lyrics=["Lie-", "Got-"]),
        Event(x=1, duration=Fraction(1), pitches=[Pitch("D", 5)], lyrics=["be", "tes"]),
    ]
    xml = to_musicxml(Score(title="T", parts=[Part(name="Ch", measures=[m])]))
    assert '<lyric number="1"><syllabic>begin</syllabic><text>Lie</text></lyric>' in xml
    assert '<lyric number="2"><syllabic>end</syllabic><text>tes</text></lyric>' in xml
