"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.content import sync_topics  # noqa: E402
from app.extensions import db  # noqa: E402

SEED_FILE = ROOT / "seed" / "questions.json"

SAMPLE_QUESTIONS = [
    {
        "topic": "cell-biology",
        "type": "multiple_choice",
        "question_text": "Which organelle is the site of aerobic respiration?",
        "correct_answer": "Mitochondrion",
        "choices": ["Mitochondrion", "Ribosome", "Golgi apparatus", "Nucleus"],
        "marks": 1,
        "explanation": "The mitochondrion carries out oxidative phosphorylation.",
        "tags": ["organelles"],
        "difficulty": "easy",
    },
    {
        "topic": "molecular-biology",
        "type": "short_answer",
        "question_text": "State the bond that joins amino acids in a polypeptide.",
        "correct_answer": "Peptide bond",
        "marks": 1,
        "explanation": "A condensation reaction between the amine and carboxyl groups.",
        "tags": ["proteins"],
        "difficulty": "easy",
    },
    {
        "topic": "genetics",
        "type": "short_answer",
        "question_text": (
            "Outline the role of helicase in DNA replication. "
            "It unwinds the double helix; it breaks hydrogen bonds between bases"
        ),
        "correct_answer": (
            "Unwinds the double helix; breaks hydrogen bonds between complementary bases"
        ),
        "marks": 2,
        "explanation": "Helicase separates the two strands ahead of the replication fork.",
        "tags": ["replication", "enzymes"],
        "difficulty": "medium",
    },
    {
        "topic": "metabolism-cell-respiration-photosynthesis",
        "type": "calculation",
        "question_text": "A reaction produces 0.05 mol of gas in 20 seconds. Calculate the rate in mol s-1.",
        "correct_answer": "0.0025 mol s-1",
        "marks": 2,
        "explanation": "Rate is amount divided by time: 0.05 / 20.",
        "tags": ["rates"],
        "difficulty": "medium",
    },
    {
        "topic": "ecology",
        "type": "true_false",
        "question_text": "Energy is recycled within an ecosystem.",
        "correct_answer": "False",
        "marks": 1,
        "explanation": "Energy flows through an ecosystem and is lost as heat; nutrients are recycled.",
        "tags": ["energy"],
        "difficulty": "easy",
    },
]


@pytest.fixture()
def app():
    """A fresh app with an in-memory database and the sample bank loaded."""
    application = create_app("testing")
    with application.app_context():
        db.create_all()
        sync_topics()
        db.session.commit()
        from app.content import import_questions

        import_questions(SAMPLE_QUESTIONS, source="test-fixture")
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def csrf_headers(client):
    """Fetch a page and return headers carrying its CSRF token."""
    import re

    response = client.get("/quiz?count=5")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    token = match.group(1) if match else ""
    return {"X-CSRF-Token": token}


@pytest.fixture(scope="session")
def seed_path() -> Path:
    return SEED_FILE
