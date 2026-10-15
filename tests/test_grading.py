"""Tests for the answer-marking engine."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.grading import (
    CLOSE,
    PARTIAL,
    WRONG,
    grade,
    normalize_text,
    numbers_match,
    parse_number,
    similarity,
)


@dataclass
class FakeQuestion:
    """Minimal stand-in for the Question model."""

    question_type: str
    correct_answer: str
    marks: int = 1
    explanation: str = "because"
    choices: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- normalising

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Osmosis  ", "osmosis"),
        ("Naïve", "naive"),
        ("CO₂", "co2"),
        ("water—and—salts", "water and salts"),
        ("3.5 mol dm-3", "3 5 mol dm 3"),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_similarity_bounds():
    assert similarity("mitosis", "mitosis") == 100
    assert similarity("mitosis", "meiosis") > 60
    assert similarity("mitosis", "") == 0
    assert similarity("active transport", "transport that is active") > 70


# --------------------------------------------------------------------- numbers

@pytest.mark.parametrize(
    "text,value,unit",
    [
        ("0.0025 mol s-1", 0.0025, "mol s-1"),
        ("25 cm3", 25.0, "cm3"),
        ("-3.5 kJ", -3.5, "kj"),
        ("12.5%", 12.5, "%"),
        ("1.2e-3 mol", 0.0012, "mol"),
        ("no numbers here", None, None),
    ],
)
def test_parse_number(text, value, unit):
    parsed = parse_number(text)
    if value is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed.value == pytest.approx(value)
        assert parsed.unit == unit


@pytest.mark.parametrize(
    "expected,actual,ok",
    [
        ("0.0025 mol s-1", "0.0025 mol s-1", True),
        ("0.0025 mol s-1", "0.00250 mol s-1", True),
        ("10 cm3", "10 cm3", True),
        ("10 cm3", "0.00001 m3", True),  # unit prefix conversion
        ("10 cm3", "10 mm3", False),  # genuinely different volume
        ("2 mol", "2 g", False),  # unit mismatch
        ("0.5", "0.5001", True),  # within 1% tolerance
        ("0.5", "0.6", False),
    ],
)
def test_numbers_match(expected, actual, ok):
    matched, _why = numbers_match(expected, actual)
    assert matched is ok


def test_zero_expected():
    assert numbers_match("0", "0")[0] is True
    assert numbers_match("0", "0.001")[0] is False


# --------------------------------------------------------------- exact answers

def test_multiple_choice_exact_and_letter():
    question = FakeQuestion(
        question_type="multiple_choice",
        correct_answer="Mitochondrion",
        choices=["Mitochondrion", "Ribosome", "Golgi", "Nucleus"],
    )
    assert grade(question, "Mitochondrion").correct is True
    assert grade(question, "mitochondrion").correct is True
    assert grade(question, "  Mitochondrion ").correct is True
    assert grade(question, "A").correct is True  # letter A maps to choice 1
    assert grade(question, "Ribosome").correct is False
    assert grade(question, "Ribosome").marks_awarded == 0


def test_true_false():
    question = FakeQuestion(question_type="true_false", correct_answer="False")
    assert grade(question, "false").correct is True
    assert grade(question, "FALSE").correct is True
    assert grade(question, "True").correct is False


def test_empty_answer_is_never_correct():
    for qtype, answer in [
        ("multiple_choice", "Mitochondrion"),
        ("short_answer", "Peptide bond"),
        ("calculation", "42"),
        ("true_false", "True"),
    ]:
        question = FakeQuestion(question_type=qtype, correct_answer=answer, marks=2)
        verdict = grade(question, "")
        assert verdict.correct is False
        assert verdict.marks_awarded == 0
        assert verdict.verdict == WRONG
        assert grade(question, "   ").correct is False
        assert grade(question, None).correct is False


# ------------------------------------------------------------------ calculation

def test_calculation_marking():
    question = FakeQuestion(
        question_type="calculation",
        correct_answer="0.0025 mol s-1",
        marks=2,
    )
    assert grade(question, "0.0025 mol s-1").correct is True
    # Right number with the wrong unit earns partial credit...
    assert grade(question, "0.0025 mol dm-3").verdict == PARTIAL
    assert grade(question, "0.0025 mol dm-3").marks_awarded == 1
    # ...but a bare number with no unit cannot be credited.
    assert grade(question, "0.0025").verdict == WRONG
    assert grade(question, "0.0025").marks_awarded == 0
    assert grade(question, "0.25 mol s-1").verdict == WRONG
    assert grade(question, "banana").verdict == WRONG


# ---------------------------------------------------------------- short answer

def test_short_answer_single_mark():
    question = FakeQuestion(
        question_type="short_answer", correct_answer="Peptide bond", marks=1
    )
    assert grade(question, "Peptide bond").correct is True
    assert grade(question, "a peptide bond").correct is True
    assert grade(question, "hydrogen bond").correct is False


def test_short_answer_multi_point_partial_credit():
    question = FakeQuestion(
        question_type="short_answer",
        correct_answer="Unwinds the double helix; breaks hydrogen bonds between bases",
        marks=2,
    )
    both = grade(question, "It unwinds the double helix and breaks hydrogen bonds between bases")
    assert both.correct is True
    assert both.marks_awarded == 2

    one = grade(question, "It unwinds the double helix")
    assert one.marks_awarded == 1
    assert one.verdict == PARTIAL
    assert one.correct is False
    assert one.keywords_missed

    none = grade(question, "It copies the DNA")
    assert none.marks_awarded == 0
    assert none.verdict in {WRONG, CLOSE}


def test_short_answer_reports_matched_and_missed_points():
    question = FakeQuestion(
        question_type="short_answer",
        correct_answer="Temperature; pH; substrate concentration",
        marks=3,
    )
    verdict = grade(question, "Temperature and pH affect the rate")
    assert verdict.marks_awarded >= 1
    assert len(verdict.keywords_matched) >= 1
    assert verdict.keywords_matched != verdict.keywords_missed


def test_near_miss_is_labelled_close_not_correct():
    """A semantically close but inaccurate answer must not earn full marks.

    "drops" is not "decreases" as far as the mark scheme is concerned, so the
    answer is flagged as close for review rather than marked correct.
    """
    question = FakeQuestion(
        question_type="short_answer",
        correct_answer="The rate of reaction increases then decreases",
        marks=2,
    )
    verdict = grade(question, "The rate of reaction increases then drops")
    assert verdict.correct is False
    assert verdict.marks_awarded == 0
    assert verdict.verdict == CLOSE


def test_grade_returns_serialisable_verdict():
    question = FakeQuestion(question_type="short_answer", correct_answer="Peptide bond")
    payload = grade(question, "peptide bond").as_dict()
    assert set(payload) >= {"correct", "verdict", "score", "marks_awarded", "feedback"}
    assert isinstance(payload["correct"], bool)


def test_unknown_question_type_falls_back_to_short_answer():
    question = FakeQuestion(question_type="essay", correct_answer="Peptide bond", marks=1)
    assert grade(question, "Peptide bond").correct is True
