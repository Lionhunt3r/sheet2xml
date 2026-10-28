"""Engine-independent score model that both recognizers fill and the writer reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction


@dataclass
class Pitch:
    step: str  # "C".."B"
    octave: int
    alter: int = 0
    tie_start: bool = False
    tie_stop: bool = False

    @property
    def diatonic(self) -> int:
        return self.octave * 7 + "CDEFGAB".index(self.step)


@dataclass
class Event:
    x: float
    duration: Fraction  # sounding length in quarter notes (after dots and tuplets)
    pitches: list[Pitch] = field(default_factory=list)  # empty = rest
    voice: int = 1
    staff: int = 1  # 1-based staff inside the part
    base: Fraction = Fraction(1)  # notated value without dots/tuplets, in quarters
    dots: int = 0
    tuplet: tuple[int, int] | None = None  # (actual, normal), e.g. (3, 2)
    grace: bool = False
    full_measure: bool = False
    stem: str | None = None  # "up" / "down"
    articulations: list[str] = field(default_factory=list)
    notehead: str | None = None  # "slash" for rhythm slashes
    lyrics: list[str] = field(default_factory=list)  # one syllable per verse ("" = none); "-" at the end: word goes on
    hidden: bool = False  # filler rest added to complete a voice

    @property
    def is_rest(self) -> bool:
        return not self.pitches


@dataclass
class Clef:
    sign: str  # "G", "F", "C", "percussion"
    line: int
    octave_change: int = 0


@dataclass
class Direction:
    x: float
    staff: int
    kind: str  # "dynamic", "words", "tempo", "harmony", "rehearsal"
    text: str
    above: bool = True


@dataclass
class Measure:
    number: int
    events: list[Event] = field(default_factory=list)
    time: tuple[int, int] | None = None  # set when it changes (or first measure)
    time_symbol: str | None = None  # "common" / "cut"
    key: int | None = None  # fifths, set when it changes
    clefs: dict[int, Clef] = field(default_factory=dict)  # staff -> clef at measure start
    clef_changes: list[tuple[float, int, Clef]] = field(default_factory=list)  # (x, staff, clef)
    directions: list[Direction] = field(default_factory=list)
    barline: str | None = None  # right barline: "final", "double", "repeat-end"
    repeat_start: bool = False  # left barline opens a repeat
    expected: Fraction | None = None  # length implied by the time signature
    rest_measures: int = 1  # >1 for a multi-measure rest placeholder
    partial: bool = False  # deliberately short: pickup, end of movement, split at a repeat
    implicit: bool = False  # second half of a split measure: does not count in numbering
    warnings: list[str] = field(default_factory=list)


@dataclass
class Part:
    name: str
    abbreviation: str = ""
    staves: int = 1
    measures: list[Measure] = field(default_factory=list)
    transpose: tuple[int, int] | None = None  # (diatonic, chromatic) written -> sounding


@dataclass
class Score:
    title: str = ""
    subtitle: str = ""
    composer: str = ""
    lyricist: str = ""
    arranger: str = ""
    parts: list[Part] = field(default_factory=list)
    source: str = ""  # "vector" / "omr"
    warnings: list[str] = field(default_factory=list)
