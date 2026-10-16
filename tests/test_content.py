"""Tests for content import/export helpers and the bundled seed bank.

The seed tests are the ones that protect the shipped question bank: they fail if
anyone edits ``seed/questions.json`` into an invalid state.
"""

from __future__ import annotations

import json

import pytest

from app.content import (
    build_question,
    export_questions,
    fingerprint,
    get_or_create_topic,
    import_questions,
    load_payload,
    normalize_topic,
    sync_topics,
    validate_question,
)
from app.extensions import db
from app.models import Question, Topic
from app.topics import TOPIC_CATALOGUE, TOPICS_BY_SLUG


# ------------------------------------------------------------------ normalising

@pytest.mark.parametrize(
    "value,expected",
    [
        ("cell-biology", "cell-biology"),
        ("Cell Biology", "cell-biology"),
        ("cell_biology", "cell-biology"),
        ("genetics", "genetics"),
        ("human physiology", "human-physiology"),
        ("nucleic acids", "nucleic-acids"),
        # 2025 subtopic slugs map to themselves, via the exact-slug path.
        ("bio-a11-water", "bio-a11-water"),
        ("bio-b32-transport", "bio-b32-transport"),
        # The 2024 topics carry no syllabus reference, so a bare code matches
        # nothing rather than guessing a topic.
        ("A2", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_topic(value, expected):
    assert normalize_topic(value) == expected


def test_every_catalogue_slug_is_unique_and_orderable():
    slugs = [t.slug for t in TOPIC_CATALOGUE]
    assert len(slugs) == len(set(slugs))
    assert all(slug == slug.lower() for slug in slugs)
    assert all(t.title and t.description for t in TOPIC_CATALOGUE)


# ------------------------------------------------------------------ validation

def test_validate_question_accepts_a_good_question():
    assert validate_question(
        {
            "question_text": "State the function of the mitochondrion.",
            "correct_answer": "Aerobic respiration",
            "type": "short_answer",
            "marks": 1,
            "difficulty": "easy",
        },
        0,
    ) == []


@pytest.mark.parametrize(
    "payload,problem_fragment",
    [
        ({"correct_answer": "x", "question_text": "tiny"}, "question_text"),
        ({"question_text": "A long enough question about cells."}, "correct_answer"),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "x",
                "type": "not_a_type",
            },
            "unknown type",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "x",
                "difficulty": "impossible",
            },
            "unknown difficulty",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "x",
                "marks": "many",
            },
            "not an integer",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "x",
                "type": "multiple_choice",
                "choices": ["only one"],
            },
            "at least 2 choices",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "absent",
                "type": "multiple_choice",
                "choices": ["a", "b", "c", "d"],
            },
            "not one of the choices",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "",
                "type": "multiple_choice",
                "choices": ["a", "b", "c", "d"],
            },
            "needs an answer among its choices",
        ),
        (
            {
                "question_text": "A long enough question about cells.",
                "correct_answer": "sometimes",
                "type": "true_false",
            },
            "must be True or False",
        ),
    ],
)
def test_validate_question_rejects_bad_payloads(payload, problem_fragment):
    problems = validate_question(payload, 0)
    assert any(problem_fragment in problem for problem in problems), problems


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("State the role of helicase!") == fingerprint("state the role of HELICASE")
    assert fingerprint("Question one") != fingerprint("Question two")


# ------------------------------------------------------------------- building

def test_build_question_sanitises_and_normalises():
    built = build_question(
        {
            "topic": "genetics",
            "type": "MULTIPLE_CHOICE",
            "question_text": "<p>Which base pairs with adenine?</p><script>x</script>",
            "correct_answer": "thymine",
            "choices": ["Thymine", "Guanine", "Cytosine", "Uracil"],
            "marks": "2",
            "difficulty": "HARD",
            "tags": "dna, replication",
            "explanation": "A pairs with T.",
        }
    )
    assert built["question_type"] == "multiple_choice"
    assert built["difficulty"] == "hard"
    assert built["marks"] == 2
    assert "<script" not in built["question_text"]
    assert json.loads(built["tags_json"]) == ["dna", "replication"]
    # The stored answer must match its choice exactly, including case.
    assert built["correct_answer"] == "Thymine"


def test_build_question_clamps_marks():
    assert build_question({"marks": 0, "correct_answer": "x", "question_text": "y" * 10})["marks"] == 1
    assert build_question({"marks": 500, "correct_answer": "x", "question_text": "y" * 10})["marks"] == 20


def test_build_question_defaults_unknown_type():
    built = build_question(
        {"question_text": "Valid question text.", "correct_answer": "x", "type": "essay"}
    )
    assert built["question_type"] == "short_answer"


# -------------------------------------------------------------------- importing

def test_sync_topics_is_idempotent(app):
    with app.app_context():
        before = db.session.query(Topic).count()
        sync_topics()
        db.session.commit()
        assert db.session.query(Topic).count() == before


def test_get_or_create_topic_reuses_existing(app):
    with app.app_context():
        topic, created = get_or_create_topic("genetics")
        assert created is False
        assert topic.slug == "genetics"

        topic, created = get_or_create_topic("a completely unknown topic name")
        assert created is True
        assert topic.slug


def test_import_skips_duplicates_within_one_payload(app):
    with app.app_context():
        payload = {
            "topic": "ecology",
            "type": "short_answer",
            "question_text": "Define the term habitat for an organism.",
            "correct_answer": "The place where an organism lives",
            "marks": 1,
            "explanation": "Habitat is the physical location.",
            "difficulty": "easy",
        }
        report = import_questions([payload, payload], source="test")
        assert report.created == 1
        assert report.skipped == 1


def test_question_source_survives_the_file_level_default(app):
    """A question's own source label must not be replaced by the file name.

    The importers tag every question they emit (``rdojo:``, ``village:``); that
    label is the only way to trace a question back to where it came from, so an
    import must not collapse it onto ``file:<name>``.
    """
    from app.models import Question

    with app.app_context():
        report = import_questions(
            [
                {
                    "type": "multiple_choice",
                    "question_text": "Which pigment absorbs red and blue light?",
                    "correct_answer": "Chlorophyll",
                    "choices": ["Chlorophyll", "Carotene", "Xanthophyll", "Phycoerythrin"],
                    "marks": 1,
                    "explanation": "Chlorophyll absorbs red and blue light.",
                    "difficulty": "easy",
                    "source": "village:BI0001",
                },
                {
                    "type": "short_answer",
                    "question_text": "State where the light-dependent reactions occur.",
                    "correct_answer": "Thylakoid membrane",
                    "marks": 1,
                    "explanation": "The thylakoid membrane.",
                    "difficulty": "easy",
                },
            ],
            source="file:additional.json",
        )
        assert report.created == 2

        tagged = Question.query.filter_by(source="village:BI0001").one()
        assert tagged.question_text.startswith("Which pigment")

        # No source of its own: the file-level label is still the fallback.
        assert Question.query.filter_by(source="file:additional.json").count() == 1


def test_import_reports_rejected_count(app):
    with app.app_context():
        report = import_questions(
            [{"question_text": "x"}, {"question_text": "y"}], source="test"
        )
        assert report.created == 0
        assert report.rejected == 2
        assert len(report.errors) >= 2


def test_import_rejects_overlong_text(app):
    with app.app_context():
        report = import_questions(
            [
                {
                    "question_text": f"Valid opening question about cells. {'x' * 6000}",
                    "correct_answer": "answer",
                    "marks": 1,
                }
            ],
            source="test",
        )
        # Either rejected or stored truncated, but never a crash.
        assert report.created + report.rejected == 1


def test_export_matches_bank(app):
    with app.app_context():
        payload = export_questions()
        count = db.session.query(Question).count()
        assert payload["meta"]["count"] == count
        assert len(payload["questions"]) == count


def test_load_payload_accepts_jsonl(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"question_text": "One?", "correct_answer": "a"}),
                json.dumps({"question_text": "Two?", "correct_answer": "b"}),
            ]
        )
    )
    questions, meta = load_payload(path)
    assert len(questions) == 2
    assert meta == {}


def test_load_payload_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_payload(tmp_path / "nope.json")


# --------------------------------------------------------------- the seed bank

@pytest.fixture(scope="module")
def seed_payload(seed_path):
    return json.loads(seed_path.read_text(encoding="utf-8"))


def test_seed_file_exists(seed_path):
    assert seed_path.exists(), f"seed file missing at {seed_path}"


def test_seed_meta_is_consistent(seed_payload):
    meta = seed_payload["meta"]
    assert meta["count"] == len(seed_payload["questions"])
    assert meta["count"] >= 100


def test_every_seed_question_is_valid(seed_payload):
    """Any edit to the seed bank that breaks the schema fails here."""
    problems: list[str] = []
    for index, question in enumerate(seed_payload["questions"]):
        problems.extend(validate_question(question, index))
    assert problems == [], problems[:20]


def test_seed_topics_are_known_slugs(seed_payload):
    slugs = {q["topic"] for q in seed_payload["questions"]}
    unknown = slugs - set(TOPICS_BY_SLUG)
    assert not unknown, f"unknown topic slugs in seed: {unknown}"


def test_seed_covers_every_catalogue_topic(seed_payload):
    covered = {q["topic"] for q in seed_payload["questions"]}
    missing = set(TOPICS_BY_SLUG) - covered
    assert not missing, f"topics with no seed questions: {missing}"


def test_seed_has_a_healthy_type_mix(seed_payload):
    types: dict[str, int] = {}
    for question in seed_payload["questions"]:
        types[question["type"]] = types.get(question["type"], 0) + 1
    assert set(types) == {"multiple_choice", "short_answer", "true_false", "calculation"}
    assert types["multiple_choice"] >= 20
    assert types["short_answer"] >= 20


def test_seed_questions_have_explanations(seed_payload):
    missing = [q["question_text"][:40] for q in seed_payload["questions"] if not q["explanation"]]
    assert not missing, missing


def test_seed_question_texts_are_unique(seed_payload):
    texts = [q["question_text"] for q in seed_payload["questions"]]
    assert len(texts) == len(set(texts))


def test_seed_multiple_choice_answers_are_options(seed_payload):
    for question in seed_payload["questions"]:
        if question["type"] == "multiple_choice":
            assert question["correct_answer"] in question["choices"], question["question_text"][:60]
            assert len(set(question["choices"])) == len(question["choices"])


def test_seed_imports_into_an_empty_database(app, seed_payload):
    """The shipped bank must import cleanly through the real import path."""
    with app.app_context():
        db.session.query(Question).delete()
        db.session.commit()

        report = import_questions(seed_payload["questions"], source="seed-test")
        assert report.created == len(seed_payload["questions"])
        assert report.rejected == 0, report.errors[:10]
        assert db.session.query(Question).count() == len(seed_payload["questions"])

        # Every question ended up with a known topic.
        slugs = {row[0] for row in db.session.query(Topic.slug).all()}
        assert {q["topic"] for q in seed_payload["questions"]} <= slugs
