"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.auth import rate_limit_reset  # noqa: E402
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


def make_user(application, email: str, password: str = "correct-horse-battery", *, admin: bool = False):
    """Create an account directly, bypassing the sign-up form."""
    from app.models import User

    with application.app_context():
        user = User(email=email.lower(), is_admin=admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, email: str, password: str = "correct-horse-battery"):
    """Sign in through the real form, so CSRF and sessions are exercised."""
    import re

    page = client.get("/auth/login")
    token = re.search(
        r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    return client.post(
        "/auth/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=True,
    )


@pytest.fixture()
def app():
    """A fresh app with an in-memory database, the sample bank and one account."""
    from app.models import User

    rate_limit_reset()
    application = create_app("testing")
    application.config.update(
        ALLOW_REGISTRATION=True,
        REGISTRATION_CODE=None,
        WTF_CSRF_ENABLED=False,
    )
    with application.app_context():
        db.create_all()
        sync_topics()
        db.session.commit()
        from app.content import import_questions

        import_questions(SAMPLE_QUESTIONS, source="test-fixture")

        #: The default account most tests act as.
        owner = User(email="student@example.com", display_name="Student")
        owner.set_password("correct-horse-battery")
        db.session.add(owner)
        db.session.commit()
        application.config["TEST_USER_ID"] = owner.id

    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()
    rate_limit_reset()


@pytest.fixture()
def client(app):
    """A signed-in client. Prefer this unless a test is about authentication."""
    test_client = app.test_client()
    login(test_client, "student@example.com")
    return test_client


@pytest.fixture()
def anon_client(app):
    """A client with no session, for testing what visitors can reach."""
    return app.test_client()


@pytest.fixture()
def second_client(app):
    """A second, independent account — used to prove progress is per user."""
    from app.models import User

    with app.app_context():
        other = User(email="other@example.com", display_name="Other")
        other.set_password("correct-horse-battery")
        db.session.add(other)
        db.session.commit()

    test_client = app.test_client()
    login(test_client, "other@example.com")
    return test_client


@pytest.fixture()
def admin_client(app):
    """A signed-in administrator, for the question-bank endpoints.

    Import and export can replace or read the whole bank, so they are restricted
    to administrators rather than any signed-in account.
    """
    from app.models import User

    with app.app_context():
        admin = User(email="admin@example.com", display_name="Admin", is_admin=True)
        admin.set_password("correct-horse-battery")
        db.session.add(admin)
        db.session.commit()

    test_client = app.test_client()
    login(test_client, "admin@example.com")
    return test_client


@pytest.fixture()
def admin_headers(admin_client):
    """CSRF headers for the administrator client."""
    import re

    response = admin_client.get("/quiz?count=5")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    return {"X-CSRF-Token": match.group(1) if match else ""}


@pytest.fixture()
def csrf_headers(client):
    """Headers carrying the signed-in session's CSRF token."""
    import re

    response = client.get("/quiz?count=5")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    token = match.group(1) if match else ""
    return {"X-CSRF-Token": token}


@pytest.fixture(scope="session")
def seed_path() -> Path:
    return SEED_FILE
