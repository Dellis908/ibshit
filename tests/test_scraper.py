"""Tests for the optional scraper pipeline (no network access required)."""

from __future__ import annotations

import json

import pytest

from scraper import build_seed, fetch_links, pdf_parser

SAMPLE_PAPER = """
Smearchive / pirateib
Page 1 of 12

1.  The diagram shows a cell organelle.
    State the name of the organelle shown.                                    [1]

2.  Which process requires ATP?
    A  Osmosis
    B  Active transport
    C  Simple diffusion
    D  Facilitated diffusion                                                   [1]

3.  A student measured the rate of an enzyme-controlled reaction.
    Calculate the rate of reaction in mol s-1 if 0.05 mol of product
    was produced in 20 seconds. Show your working.                             [2]

4.  Explain how the structure of the small intestine is adapted for
    absorption of glucose.                                                     [4]

Page 2 of 12
5.  Outline the role of the xylem in transport.                                [2]
"""

SAMPLE_MARK_SCHEME = """
Mark scheme

1.  mitochondrion                                                              [1]

2.  B                                                                          [1]

3.  0.0025 mol s-1;
    award 1 mark for correct substitution                                      [2]

4.  villi increase surface area;
    microvilli further increase surface area;
    rich blood supply maintains concentration gradient;
    thin epithelium shortens diffusion distance                                [4]

5.  transports water and mineral ions;
    from roots to leaves                                                        [2]
"""


# ------------------------------------------------------------------- cleaning

def test_clean_text_joins_hyphenated_words():
    assert "photosynthesis" in pdf_parser.clean_text("photo-\nsynthesis")


def test_clean_text_normalises_unicode():
    cleaned = pdf_parser.clean_text("the cell\u2019s 5\u22123 \u201cquoted\u201d")
    assert "'" in cleaned
    assert "-" in cleaned
    assert '"' in cleaned


def test_is_noise_detects_page_furniture():
    assert pdf_parser.is_noise("")
    assert pdf_parser.is_noise("  12  ")
    assert pdf_parser.is_noise("Page 3 of 20")
    assert pdf_parser.is_noise("TURN OVER")
    assert pdf_parser.is_noise("© Some Publisher")
    assert not pdf_parser.is_noise("State the name of the organelle shown.")


# --------------------------------------------------------------------- marks

@pytest.mark.parametrize(
    "text,expected",
    [
        ("State the name. [1]", 1),
        ("Explain this process. (4 marks)", 4),
        ("Calculate the rate. [2 marks]", 2),
        ("No marks given here", 1),
        ("Two parts [2] and [3]", 3),
    ],
)
def test_detect_marks(text, expected):
    assert pdf_parser.detect_marks(text) == expected


# --------------------------------------------------------------- question type

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Calculate the rate of reaction.", "calculation"),
        ("State the name of the organelle.", "short_answer"),
        ("Explain how this happens.", "short_answer"),
        ("Suggest a reason for the result.", "short_answer"),
        ("Decide whether the statement is true or false.", "true_false"),
        ("Describe the process.", "short_answer"),
    ],
)
def test_detect_type(text, expected):
    assert pdf_parser.detect_type(text) == expected


# ------------------------------------------------------------------ choices

def test_extract_choices_splits_options():
    block = "Which process requires ATP?\nA  Osmosis\nB  Active transport\nC  Diffusion\nD  Facilitated diffusion"
    choices, stem = pdf_parser.extract_choices(block)
    assert choices == ["Osmosis", "Active transport", "Diffusion", "Facilitated diffusion"]
    assert "Which process" in stem


def test_extract_choices_returns_nothing_without_options():
    choices, stem = pdf_parser.extract_choices("Explain how the villi absorb glucose.")
    assert choices == []
    assert "villi" in stem


def test_answer_from_choices_maps_letter():
    choices = ["Osmosis", "Active transport", "Diffusion", "Facilitated diffusion"]
    answer, warnings = pdf_parser.answer_from_choices(choices, "B")
    assert answer == "Active transport"
    assert warnings == []

    missing, warnings = pdf_parser.answer_from_choices(choices, "Z")
    assert missing == ""
    assert warnings


# -------------------------------------------------------------------- parsing

def test_parse_questions_finds_every_question():
    questions = pdf_parser.parse_questions(SAMPLE_PAPER, topic="cell-biology", source="paper.pdf")
    assert len(questions) == 5
    assert all(q.question_text for q in questions)


def test_parse_questions_reads_marks_and_types():
    questions = pdf_parser.parse_questions(
        SAMPLE_PAPER, topic="cell-biology", source="paper.pdf"
    )
    # Every question gets a traceable source label.
    assert all(q.source.startswith("paper.pdf#q") for q in questions)
    assert all(q.marks >= 1 for q in questions)

    # Question 2 is multiple choice: its four options must be captured.
    mcq = [q for q in questions if q.choices]
    assert len(mcq) == 1
    assert len(mcq[0].choices) == 4
    assert mcq[0].question_type == "multiple_choice"
    assert mcq[0].marks == 1

    # Question 3 is a calculation.
    calc = [q for q in questions if q.question_type == "calculation"]
    assert calc and calc[0].marks == 2

    # Question 4 is a four-mark explanation.
    long_answer = [q for q in questions if q.marks == 4]
    assert len(long_answer) == 1
    assert long_answer[0].question_type == "short_answer"


def test_parse_questions_strips_mark_allocations_from_text():
    questions = pdf_parser.parse_questions(SAMPLE_PAPER)
    for question in questions:
        assert "[1]" not in question.question_text
        assert "[2]" not in question.question_text
        assert "[4]" not in question.question_text


def test_parse_questions_ignores_page_furniture():
    questions = pdf_parser.parse_questions(SAMPLE_PAPER)
    for question in questions:
        lowered = question.question_text.lower()
        assert "smearchive" not in lowered
        assert "page 1 of" not in lowered
        assert "turn over" not in lowered


def test_parse_questions_skips_short_fragments():
    text = "1.  ok\n2.  This is a properly formed question about cell structure."
    questions = pdf_parser.parse_questions(text)
    assert len(questions) == 1
    assert "properly formed" in questions[0].question_text


def test_parse_questions_flags_figure_references():
    text = "1.  Using the graph below, describe how the rate changes with temperature."
    questions = pdf_parser.parse_questions(text)
    assert questions
    assert any("figure" in warning or "diagram" in warning for warning in questions[0].warnings)
    assert questions[0].confidence < 1.0


def test_decimal_numbers_do_not_start_questions():
    """A number like "2.5 cm" inside a stem must not be read as question 2."""
    text = (
        "1.  A cube of tissue measures 2.5 cm on each side.\n"
        "    Calculate its surface area to volume ratio."
    )
    questions = pdf_parser.parse_questions(text)
    assert len(questions) == 1
    assert "2.5 cm" in questions[0].question_text


# --------------------------------------------------------------- mark scheme

def test_parse_mark_scheme_extracts_answers():
    answers = pdf_parser.parse_mark_scheme(SAMPLE_MARK_SCHEME)
    assert 1 in answers
    assert "mitochondrion" in answers[1][0].lower()
    assert "B" in answers[2][0]
    assert "0.0025" in answers[3][0]
    assert 5 in answers


def test_parse_mark_scheme_keeps_multiple_points():
    answers = pdf_parser.parse_mark_scheme(SAMPLE_MARK_SCHEME)
    answer, _warnings = answers[4]
    assert answer.count(";") >= 1


def test_parse_questions_merges_mark_scheme():
    answers = pdf_parser.parse_mark_scheme(SAMPLE_MARK_SCHEME)
    questions = pdf_parser.parse_questions(SAMPLE_PAPER, answers=answers)
    answered = [q for q in questions if q.correct_answer]
    assert len(answered) == 5


def test_question_numbers_extracted_from_source():
    assert build_seed._question_number("question 12") == 12
    assert build_seed._question_number("paper.pdf") is None


# ------------------------------------------------------------- topic guessing

@pytest.mark.parametrize(
    "filename,expected",
    [
        ("cell_biology_2021_hl.pdf", "cell-biology"),
        ("genetics_paper1.pdf", "genetics"),
        ("ecology_topic_questions.pdf", "ecology"),
        ("human_physiology_ms.pdf", "human-physiology"),
        ("photosynthesis_respiration.pdf", "metabolism-cell-respiration-photosynthesis"),
    ],
)
def test_topic_from_filename(filename, expected):
    assert build_seed.topic_from_filename(filename) == expected


# ---------------------------------------------------------------- build stage

def test_build_from_texts_produces_importable_payload():
    questions, report = build_seed.build_from_texts({"cell_biology_2021.pdf": SAMPLE_PAPER})
    assert report.total == len(questions)
    assert report.total >= 4
    assert report.by_topic.get("cell-biology", 0) >= 4

    for question in questions:
        assert question["topic"] == "cell-biology"
        assert question["question_text"]
        assert isinstance(question["marks"], int)
        assert question["_confidence"] <= 1.0


def test_build_from_texts_deduplicates():
    _questions, report = build_seed.build_from_texts(
        {"a.pdf": SAMPLE_PAPER, "b.pdf": SAMPLE_PAPER}
    )
    # The same paper twice must not double the bank.
    assert report.total < 10


def test_write_importable_skips_unanswered():
    questions = [
        {"question_text": "A valid question about cells.", "correct_answer": "yes"},
        {"question_text": "An unanswered question about cells.", "correct_answer": ""},
    ]
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "out.json"
        build_seed.write_importable(questions, path)
        written = json.loads(path.read_text())
        assert len(written) == 1
        assert written[0]["correct_answer"] == "yes"


def test_write_review_creates_markdown():
    import pathlib
    import tempfile

    questions = [
        {
            "question_text": "Which organelle?",
            "topic": "cell-biology",
            "correct_answer": "",
            "_confidence": 0.3,
            "_warnings": ["no answer found"],
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "review.md"
        build_seed.write_review(questions, path)
        content = path.read_text()
        assert "Which organelle?" in content
        assert "no answer found" in content


# ------------------------------------------------------------------- download

def test_filename_for_sanitises_urls():
    assert download_filename("https://example.org/a/b/cell%20biology.pdf") == "cell biology.pdf"


def test_filename_for_appends_pdf_extension():
    assert download_filename("https://example.org/a/questions").endswith(".pdf")


def download_filename(url):
    from scraper.download import filename_for

    return filename_for(url)


# --------------------------------------------------------------------- links

def test_pdf_link_regex_matches_relative_and_absolute():
    html = (
        '<a href="/files/cell.pdf">cells</a>'
        '<a href="https://example.org/x/genes.pdf?token=1">genes</a>'
        '<a href="notes.docx">notes</a>'
    )
    matches = fetch_links._PDF_HREF_RE.findall(html)
    assert matches == ["/files/cell.pdf", "https://example.org/x/genes.pdf?token=1"]


def test_dedupe_preserves_order():
    urls = ["a", "b", "a", "c", "b"]
    assert fetch_links._dedupe(urls) == ["a", "b", "c"]


def test_describe_returns_basename():
    assert fetch_links.describe("https://example.org/x/cell.pdf") == "cell.pdf"
