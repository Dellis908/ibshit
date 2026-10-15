"""Tests for the rdojo question-bank converter.

These cover the conversion rules that matter for marking: the answer must be one
of the options, mark schemes must keep their individual marking points, and
content that depends on a missing diagram must be rejected rather than shipped.
"""

from __future__ import annotations

import pytest

from scraper import rdojo


# ------------------------------------------------------------------- latex

@pytest.mark.parametrize(
    "raw,expected",
    [
        (r"H$_2$O", "H<sub>2</sub>O"),
        (r"CO$_2$ and H$_2$O", "CO<sub>2</sub> and H<sub>2</sub>O"),
        (r"$10^{-3}$ mol", "10<sup>-3</sup> mol"),
        (r"$\frac{1}{2}$ mol", "1/2 mol"),
        (r"$\alpha$-helix", "\u03b1-helix"),
        (r"$\text{CO}_2$", "CO<sub>2</sub>"),
        (r"$x \rightarrow y$", "x \u2192 y"),
        (r"$x \uparrow$", "x \u2191"),
        (r"$x \rightleftharpoons y$", "x \u21cc y"),
        (r"plain text", "plain text"),
        ("", ""),
    ],
)
def test_latex_to_html(raw, expected):
    assert rdojo.latex_to_html(raw) == expected


def test_latex_leaves_no_commands_or_delimiters():
    """No LaTeX scaffolding may reach a student."""
    rendered = rdojo.latex_to_html(
        r"$\begin{aligned} \text{CO}_2 + \text{H}_2\text{O} \rightleftharpoons "
        r"\text{H}_2\text{CO}_3 \end{aligned}$"
    )
    for artefact in ("\\", "$", "{", "}", "begin", "end", "aligned", "text"):
        assert artefact not in rendered, rendered
    assert "CO<sub>2</sub>" in rendered


# ---------------------------------------------------------------- markdown

def test_markdown_to_html_converts_bold_and_paragraphs():
    rendered = rdojo.markdown_to_html("This is **important** text.")
    assert "<strong>important</strong>" in rendered
    assert rendered.startswith("<p>")


def test_markdown_to_html_converts_tables():
    table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
    rendered = rdojo.markdown_to_html(table)
    assert "<table>" in rendered
    assert "<th>A</th>" in rendered
    assert "<td>1</td>" in rendered
    assert rendered.count("<tr>") == 3  # header + two body rows


def test_markdown_to_html_keeps_image_urls():
    rendered = rdojo.markdown_to_html("![diagram](https://example.org/x.png)")
    assert 'src="https://example.org/x.png"' in rendered


def test_markdown_to_html_drops_horizontal_rules():
    assert "---" not in rdojo.markdown_to_html("text\n\n---\n\nmore")


# --------------------------------------------------------------- comparison

def test_comparison_key_distinguishes_images():
    a = rdojo.comparison_key('<img src="https://cdn.example/a.jpeg">')
    b = rdojo.comparison_key('<img src="https://cdn.example/b.jpeg">')
    assert a != b, "two different images must not look like the same option"


def test_comparison_key_ignores_wrapping_tags():
    assert rdojo.comparison_key("<p>Mitosis</p>") == rdojo.comparison_key("Mitosis")


# ------------------------------------------------------------- mark schemes

def test_clean_mark_scheme_splits_marking_points():
    raw = (
        "- Water molecules form hydrogen bonds <marks>1 mark</marks>\n"
        "- More energy is required to break the bonds <marks>1 mark</marks>"
    )
    answer, marks = rdojo.clean_mark_scheme(raw)
    assert marks == 2
    assert answer.count(";") == 1  # one separator for two points
    assert "hydrogen bonds" in answer


def test_clean_mark_scheme_removes_mark_tags():
    answer, _marks = rdojo.clean_mark_scheme("A point <marks>1 mark</marks>")
    assert "marks>" not in answer
    assert "<" not in answer or "<sub>" in answer


def test_clean_mark_scheme_keeps_or_alternatives_in_one_point():
    answer, marks = rdojo.clean_mark_scheme(
        "- Buffers temperature change\n\n_**OR**_\n\n- Reduces fluctuations"
    )
    assert marks >= 1
    assert "or" in answer.lower()


def test_clean_mark_scheme_handles_empty_input():
    answer, marks = rdojo.clean_mark_scheme("")
    assert answer == ""
    assert marks == 1


def test_clean_mark_scheme_converts_latex():
    answer, _marks = rdojo.clean_mark_scheme(r"Forms $CO_2$ and water")
    assert "CO<sub>2</sub>" in answer


# ------------------------------------------------------------------ difficulty

@pytest.mark.parametrize(
    "value,expected",
    [(1, "easy"), (3, "easy"), (4, "medium"), (6, "medium"), (8, "hard"),
     (None, "medium"), ("nonsense", "medium"), (99, "medium")],
)
def test_difficulty_band(value, expected):
    assert rdojo.difficulty_band(value) == expected


# --------------------------------------------------------------- conversion

def _mc(**overrides) -> dict:
    question = {
        "id": "abc-123",
        "specification": "Which organelle carries out aerobic respiration?",
        "questionType": "MC",
        "level": "sl",
        "difficulty": 4,
        "questionSet": "TEACHER",
        "paper": "ib-1a",
        "metadata": {"labeledSubSubTopics": [{"title": "A2.2.1 Cell structure"}]},
        "parts": [],
        "options": [
            {"content": "Mitochondrion", "correct": True, "order": 0,
             "markscheme": "Correct: site of oxidative phosphorylation."},
            {"content": "Ribosome", "correct": False, "order": 1, "markscheme": ""},
            {"content": "Golgi apparatus", "correct": False, "order": 2, "markscheme": ""},
            {"content": "Nucleus", "correct": False, "order": 3, "markscheme": ""},
        ],
    }
    question.update(overrides)
    return question


def test_convert_multiple_choice():
    result = rdojo.convert_question(_mc(), topic_slug="bio-a22-cell-structure")
    assert len(result.questions) == 1
    question = result.questions[0]
    assert question["type"] == "multiple_choice"
    assert question["marks"] == 1
    assert question["correct_answer"] == "Mitochondrion"
    assert question["correct_answer"] in question["choices"]
    assert len(question["choices"]) == 4
    assert question["difficulty"] == "medium"
    assert "sl" in question["tags"]
    assert "a2-2-1" in question["tags"]
    assert "site of oxidative phosphorylation" in question["explanation"]


def test_convert_rejects_multiple_correct_options():
    question = _mc()
    question["options"][1]["correct"] = True
    result = rdojo.convert_question(question, topic_slug="bio-x")
    assert result.questions == []
    assert result.skipped and "correct options" in result.skipped[0]


def test_convert_rejects_duplicate_options():
    question = _mc()
    question["options"][1]["content"] = "Mitochondrion"
    result = rdojo.convert_question(question, topic_slug="bio-x")
    assert result.questions == []
    assert any("duplicate" in reason for reason in result.skipped)


def test_convert_keeps_distinct_image_options():
    """Four different diagram options are four distinct choices, not duplicates."""
    question = _mc()
    question["options"] = [
        {"content": f'<img src="https://cdn.example/{n}.jpeg" alt="x">',
         "correct": n == 2, "order": n, "markscheme": ""}
        for n in range(4)
    ]
    result = rdojo.convert_question(question, topic_slug="bio-x")
    assert len(result.questions) == 1
    assert len(set(result.questions[0]["choices"])) == 4


def test_convert_rejects_multiple_choice_without_stem():
    result = rdojo.convert_question(_mc(specification=""), topic_slug="bio-x")
    assert result.questions == []
    assert any("no question text" in reason for reason in result.skipped)


def test_convert_long_answer_produces_one_question_per_part():
    question = {
        "id": "la-1",
        "specification": "A study measured temperature changes in water and ethanol.",
        "questionType": "LA",
        "level": "both",
        "difficulty": None,
        "parts": [
            {"content": "Explain why water changes temperature more slowly.",
             "order": 0, "marks": 2,
             "markscheme": "- Forms hydrogen bonds <marks>1 mark</marks>\n"
                           "- More energy needed to break them <marks>1 mark</marks>"},
            {"content": "State the thermal property demonstrated.",
             "order": 1, "marks": 1,
             "markscheme": "- High specific heat capacity <marks>1 mark</marks>"},
        ],
        "options": [],
    }
    result = rdojo.convert_question(question, topic_slug="bio-a11-water")
    assert len(result.questions) == 2
    assert all(q["type"] == "short_answer" for q in result.questions)
    assert [q["marks"] for q in result.questions] == [2, 1]
    assert result.questions[0]["source"].endswith("#0")
    assert result.questions[1]["source"].endswith("#1")
    # The stem is attached to the first part so the question stands alone.
    assert "temperature" in result.questions[0]["question_text"].lower()


def test_convert_skips_parts_without_a_mark_scheme():
    question = {
        "id": "la-2",
        "specification": "Some context.",
        "questionType": "LA",
        "parts": [
            {"content": "A part with no mark scheme at all.", "order": 0, "marks": 2,
             "markscheme": ""},
        ],
        "options": [],
    }
    result = rdojo.convert_question(question, topic_slug="bio-x")
    assert result.questions == []
    assert any("no mark scheme" in reason for reason in result.skipped)


def test_convert_rejects_question_with_neither_options_nor_parts():
    result = rdojo.convert_question(
        {"id": "x", "specification": "Empty question", "options": [], "parts": []},
        topic_slug="bio-x",
    )
    assert result.questions == []
    assert any("no options and no parts" in reason for reason in result.skipped)


def test_convert_bank_reads_the_envelope():
    payload = {"json": {"questions": [_mc(), _mc(id="abc-456")], "count": "2"}}
    result = rdojo.convert_bank(payload, topic_slug="bio-x")
    assert len(result.questions) == 2


def test_convert_ignores_malformed_payload():
    assert rdojo.convert_bank({}, topic_slug="bio-x").questions == []
    assert rdojo.convert_bank({"json": {}}, topic_slug="bio-x").questions == []


# ------------------------------------------------------------- topic mapping

@pytest.mark.parametrize(
    "path,expected",
    [
        ("ib-biology-new/ib-biology-new-a-unity-and-diversity/ib-biology-new-a11-water",
         "bio-a11-water"),
        ("ib-biology-new/ib-biology-new-d-continuity-and-change/"
         "ib-biology-new-d43-climate-change", "bio-d43-climate-change"),
    ],
)
def test_topic_slug_from_path(path, expected):
    assert rdojo.topic_slug_from_path(path) == expected


# ------------------------------------------------------------ explanation text

def test_explanation_from_worked_solution_tree():
    value = {
        "methods": [
            {"steps": [
                {"type": "text", "title": "Recall", "content": "First, recall the cycle."},
                {"type": "text", "title": "Final answer",
                 "content": "Final Answer: **Carbonic acid**"},
            ]}
        ]
    }
    text = rdojo._explanation_text(value)
    assert "Carbonic acid" in text
    assert "Final Answer:" not in text
    assert "<" not in text or "<sub>" in text


def test_explanation_from_plain_string():
    assert "because" in rdojo._explanation_text("because of X")


def test_explanation_handles_missing_value():
    assert rdojo._explanation_text(None) == ""
    assert rdojo._explanation_text({}) == ""
