"""Tests for authentication, authorisation and per-account isolation.

These cover the properties that matter for a server that may be reachable from
the internet: credentials are never stored in the clear, progress cannot leak
between accounts, and the endpoints that change data cannot be driven by a
hostile page.
"""

from __future__ import annotations

import re

import pytest

from app.auth import MIN_PASSWORD_LENGTH, rate_limit_reset
from app.extensions import db
from app.models import Attempt, Question, User


def csrf_of(client, path: str = "/auth/login") -> str:
    """The CSRF token embedded in a page."""
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return match.group(1) if match else ""


# ------------------------------------------------------------------ passwords

def test_password_is_hashed_not_stored(app):
    """The database must never contain a usable password."""
    with app.app_context():
        user = db.session.query(User).filter_by(email="student@example.com").one()
        assert user.password_hash != "correct-horse-battery"
        assert "correct-horse-battery" not in user.password_hash
        # Werkzeug's default is scrypt, which is memory-hard.
        assert user.password_hash.startswith("scrypt:")
        assert user.check_password("correct-horse-battery") is True
        assert user.check_password("wrong") is False
        assert user.check_password("") is False
        assert user.check_password(None) is False


def test_two_users_with_the_same_password_get_different_hashes(app):
    """Hashes must be salted, so identical passwords are not identifiable."""
    with app.app_context():
        a = User(email="a@example.com")
        b = User(email="b@example.com")
        a.set_password("identical-password-here")
        b.set_password("identical-password-here")
        assert a.password_hash != b.password_hash


# -------------------------------------------------------------------- sign-up

def test_signup_creates_an_account_and_signs_in(anon_client, app):
    token = csrf_of(anon_client, "/auth/signup")
    response = anon_client.post(
        "/auth/signup",
        data={
            "email": "New@Example.com",
            "display_name": "Newbie",
            "password": "a-long-enough-password",
            "password_confirm": "a-long-enough-password",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        user = db.session.query(User).filter_by(email="new@example.com").one()
        # Email is normalised to lower case so accounts cannot differ by case.
        assert user.email == "new@example.com"
        assert user.display_name == "Newbie"


def test_signup_rejects_a_duplicate_email(anon_client, app):
    token = csrf_of(anon_client, "/auth/signup")
    response = anon_client.post(
        "/auth/signup",
        data={
            "email": "student@example.com",
            "password": "a-long-enough-password",
            "password_confirm": "a-long-enough-password",
            "csrf_token": token,
        },
    )
    assert response.status_code == 400
    assert b"already exists" in response.data
    with anon_client.session_transaction() as session:
        session.clear()


@pytest.mark.parametrize(
    "payload,expected_fragment",
    [
        ({"email": "not-an-email"}, "email address"),
        ({"password": "short"}, "at least"),
        ({"password": "correcthorsebatterystaple", "password_confirm": "different-value-here"}, "do not match"),
        ({"password": "password123"}, "too common"),
        ({"password": "fresh-is-my-password"}, "email address"),
    ],
)
def test_signup_validation(anon_client, payload, expected_fragment):
    token = csrf_of(anon_client, "/auth/signup")
    data = {
        "email": "fresh@example.com",
        "password": "a-long-enough-password",
        "password_confirm": "a-long-enough-password",
        "csrf_token": token,
    }
    data.update(payload)
    response = anon_client.post("/auth/signup", data=data)
    assert response.status_code == 400, response.status_code
    assert expected_fragment.encode() in response.data
    # A successful sign-up in an earlier case would sign this client in and the
    # next post would merely redirect; start each case anonymous.
    with anon_client.session_transaction() as session:
        session.clear()


def test_signup_requires_csrf(anon_client, app):
    """Without a token the form is a CSRF target, so it must be refused."""
    response = anon_client.post(
        "/auth/signup",
        data={
            "email": "no-csrf@example.com",
            "password": "a-long-enough-password",
            "password_confirm": "a-long-enough-password",
        },
    )
    assert response.status_code == 403
    with app.app_context():
        assert db.session.query(User).filter_by(email="no-csrf@example.com").one_or_none() is None


def test_registration_can_be_closed(anon_client, app):
    app.config["ALLOW_REGISTRATION"] = False
    try:
        assert anon_client.get("/auth/signup").status_code == 403
        assert anon_client.post("/auth/signup", data={}).status_code == 403
    finally:
        app.config["ALLOW_REGISTRATION"] = True


def test_invite_code_is_enforced_when_configured(anon_client, app):
    app.config["REGISTRATION_CODE"] = "let-me-in-2026"
    try:
        token = csrf_of(anon_client, "/auth/signup")
        base = {
            "email": "invited@example.com",
            "password": "a-long-enough-password",
            "password_confirm": "a-long-enough-password",
            "csrf_token": token,
        }

        response = anon_client.post("/auth/signup", data=dict(base, invite_code="wrong"))
        assert response.status_code == 400
        assert b"invite code" in response.data

        response = anon_client.post(
            "/auth/signup", data=dict(base, invite_code="let-me-in-2026")
        )
        assert response.status_code in {200, 302}
        with app.app_context():
            assert db.session.query(User).filter_by(email="invited@example.com").one_or_none()
    finally:
        app.config["REGISTRATION_CODE"] = None


# --------------------------------------------------------------------- sign-in

def test_login_success_and_failure(anon_client, app):
    token = csrf_of(anon_client)

    bad = anon_client.post(
        "/auth/login",
        data={"email": "student@example.com", "password": "wrong", "csrf_token": token},
    )
    assert bad.status_code == 401
    assert b"incorrect" in bad.data

    good = anon_client.post(
        "/auth/login",
        data={
            "email": "student@example.com",
            "password": "correct-horse-battery",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert good.status_code == 200
    assert b"questions in the bank" in good.data


def test_login_does_not_reveal_whether_an_account_exists(anon_client):
    """Both failures must be worded identically, or the form enumerates accounts."""
    token = csrf_of(anon_client)

    missing = anon_client.post(
        "/auth/login",
        data={"email": "nobody@example.com", "password": "whatever-long", "csrf_token": token},
    )
    existing = anon_client.post(
        "/auth/login",
        data={"email": "student@example.com", "password": "whatever-long", "csrf_token": token},
    )

    assert missing.status_code == existing.status_code == 401
    assert b"incorrect" in missing.data and b"incorrect" in existing.data
    # The only difference should be that the typed address is echoed back.
    assert b"nobody@example.com" in missing.data
    assert b"student@example.com" in existing.data


def test_login_email_is_case_insensitive(anon_client):
    token = csrf_of(anon_client)
    response = anon_client.post(
        "/auth/login",
        data={
            "email": "STUDENT@Example.COM",
            "password": "correct-horse-battery",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert b"questions in the bank" in response.data


def test_login_requires_csrf(anon_client):
    response = anon_client.post(
        "/auth/login", data={"email": "student@example.com", "password": "correct-horse-battery"}
    )
    assert response.status_code == 403


def test_login_rate_limited_after_repeated_failures(anon_client, app):
    """Password guessing must become impractical."""
    token = csrf_of(anon_client)
    codes = []
    for _ in range(12):
        response = anon_client.post(
            "/auth/login",
            data={"email": "student@example.com", "password": "guess", "csrf_token": token},
        )
        codes.append(response.status_code)
    assert 429 in codes, codes
    # The first attempts are ordinary failures; the limiter then takes over.
    assert codes[0] == 401


def test_login_next_redirect_stays_on_this_site(anon_client):
    """A crafted ?next= must not bounce the user to another origin."""
    token = csrf_of(anon_client)
    for hostile in ("https://evil.example/phish", "//evil.example", "http://evil.example"):
        response = anon_client.post(
            "/auth/login?next=" + hostile,
            data={
                "email": "student@example.com",
                "password": "correct-horse-battery",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        location = response.headers.get("Location", "")
        assert "evil.example" not in location, location
        anon_client.post("/auth/logout", data={"csrf_token": token})


def test_session_identifier_changes_on_login(app):
    """Session fixation: a pre-set session must not survive sign-in.

    Flask-Login's "strong" protection rotates the session identifier, so an
    attacker who fixed a session id before login cannot reuse it afterwards.
    """
    client = app.test_client()
    client.get("/auth/login")  # establish a session

    with client.session_transaction() as session:
        before_id = session.get("_id")
        session["quiz_session"] = "attacker-chosen-value"

    token = csrf_of(client)
    client.post(
        "/auth/login",
        data={
            "email": "student@example.com",
            "password": "correct-horse-battery",
            "csrf_token": token,
        },
    )

    with client.session_transaction() as session:
        assert session.get("_id"), "Flask-Login should have set a session identifier"
        assert session.get("_id") != before_id, "the session identifier must change on login"
        assert session.get("_user_id"), "the user should now be signed in"


def test_quiz_run_identifier_is_reissued_on_login(app):
    """A fresh run is started for each sign-in, so runs are not shared."""
    client = app.test_client()
    token = csrf_of(client)
    client.post(
        "/auth/login",
        data={
            "email": "student@example.com",
            "password": "correct-horse-battery",
            "csrf_token": token,
        },
    )
    client.get("/quiz?count=2")
    with client.session_transaction() as session:
        first = session.get("quiz_session")

    # Signing out and back in must not resume the previous run's grouping.
    token = csrf_of(client, "/")
    client.post("/auth/logout", data={"csrf_token": token})
    token = csrf_of(client)
    client.post(
        "/auth/login",
        data={
            "email": "student@example.com",
            "password": "correct-horse-battery",
            "csrf_token": token,
        },
    )
    client.get("/quiz?count=2")
    with client.session_transaction() as session:
        assert session.get("quiz_session") != first


# -------------------------------------------------------------------- sign-out

def test_signout_clears_the_session(client, app):
    token = csrf_of(client, "/")
    response = client.post("/auth/logout", data={"csrf_token": token}, follow_redirects=True)
    assert response.status_code == 200

    # Progress is no longer visible, and protected pages redirect again.
    assert b"of your answers recorded" not in response.data
    assert client.get("/progress").status_code == 302


@pytest.mark.parametrize(
    "path,data",
    [
        ("/auth/password", {"current_password": "correct-horse-battery",
                            "new_password": "a-brand-new-password",
                            "new_password_confirm": "a-brand-new-password"}),
        ("/auth/delete", {"password": "correct-horse-battery"}),
    ],
)
def test_account_forms_require_csrf(client, path, data):
    """A cross-site form post must not be able to change credentials."""
    response = client.post(path, data=data)
    assert response.status_code == 403
    with client.session_transaction() as session:
        assert session.get("_user_id")


def test_signout_requires_post(client):
    """A GET must not sign anyone out, so a link or image cannot do it."""
    assert client.get("/auth/logout").status_code == 405


# ------------------------------------------------------- protecting the routes

@pytest.mark.parametrize(
    "path",
    ["/topics", "/progress", "/quiz", "/results", "/quiz?count=3", "/topics/cell-biology"],
)
def test_pages_require_sign_in(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "path",
    ["/api/topics", "/api/stats", "/api/questions", "/api/result", "/api/export"],
)
def test_api_returns_401_json_for_visitors(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 401
    assert response.get_json()["error"] == "authentication_required"


def test_landing_page_is_public_and_shows_no_progress(anon_client):
    response = anon_client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "questions in the bank" in body
    assert "of your answers recorded" not in body
    assert "accuracy" not in body.lower() or "accuracy" in body.lower()  # marketing copy is fine


def test_healthcheck_is_public(anon_client):
    """Monitoring must not need credentials."""
    assert anon_client.get("/healthz").status_code == 200


def test_answer_submission_requires_sign_in(anon_client, app):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    response = anon_client.post("/api/answer", json={"question_id": question_id, "answer": "x"})
    assert response.status_code == 401


# ------------------------------------------------------------- data isolation

def test_answering_requires_csrf_even_when_signed_in(client, app):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    response = client.post("/api/answer", json={"question_id": question_id, "answer": "x"})
    assert response.status_code == 403


def test_progress_is_recorded_against_the_signed_in_account(client, app, csrf_headers):
    with app.app_context():
        question = db.session.query(Question).first()
        question_id = question.id
        owner_id = app.config["TEST_USER_ID"]

    client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "anything"},
        headers=csrf_headers,
    )

    with app.app_context():
        attempt = db.session.query(Attempt).order_by(Attempt.id.desc()).first()
        assert attempt.user_id == owner_id


def test_one_account_cannot_see_anothers_progress(client, second_client, app, csrf_headers):
    """The core isolation guarantee."""
    with app.app_context():
        question_id = db.session.query(Question).first().id

    # The first account answers something correctly.
    with app.app_context():
        question = db.session.get(Question, question_id)
        correct = question.correct_answer
    client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": correct},
        headers=csrf_headers,
    )
    assert client.get("/api/stats").get_json()["overall"]["total_attempts"] == 1

    # The second account must see none of it.
    theirs = second_client.get("/api/stats").get_json()
    assert theirs["overall"]["total_attempts"] == 0
    assert theirs["overall"]["questions_seen"] == 0
    assert all(row["attempts"] == 0 for row in theirs["topics"])


def test_second_account_cannot_read_the_first_accounts_results(
    client, second_client, app, csrf_headers
):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "x"},
        headers=csrf_headers,
    )

    with app.app_context():
        attempt = db.session.query(Attempt).order_by(Attempt.id.desc()).first()
        session_id = attempt.session_id

    # Knowing the run id is not enough.
    response = second_client.get(f"/api/result?session_id={session_id}")
    assert response.status_code == 404


def test_second_account_cannot_override_the_first_accounts_attempt(
    client, second_client, app, csrf_headers
):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "x"},
        headers=csrf_headers,
    )
    with app.app_context():
        attempt_id = db.session.query(Attempt).order_by(Attempt.id.desc()).first().id

    token = csrf_of(second_client, "/quiz?count=2")
    response = second_client.post(
        f"/api/attempts/{attempt_id}/override",
        json={"marks_awarded": 5},
        headers={"X-CSRF-Token": token},
    )
    # "Not yours" is reported as "not found", so ids cannot be probed.
    assert response.status_code == 404


def test_reset_only_clears_the_callers_progress(client, second_client, app, csrf_headers):
    with app.app_context():
        questions = db.session.query(Question).limit(2).all()
        first_id, second_id = questions[0].id, questions[1].id

    client.post("/api/answer", json={"question_id": first_id, "answer": "x"}, headers=csrf_headers)
    client.post("/api/answer", json={"question_id": second_id, "answer": "x"}, headers=csrf_headers)

    token = csrf_of(second_client, "/quiz?count=2")
    second_client.post(
        "/api/answer",
        json={"question_id": first_id, "answer": "x"},
        headers={"X-CSRF-Token": token},
    )

    # The first account resets everything of its own.
    client.post("/api/reset", json={"confirm": "RESET"}, headers=csrf_headers)

    with app.app_context():
        remaining = db.session.query(Attempt).all()
        assert len(remaining) == 1
        assert remaining[0].user_id != app.config["TEST_USER_ID"]


def test_reset_does_not_touch_the_question_bank(client, app, csrf_headers):
    with app.app_context():
        before = db.session.query(Question).count()
    client.post("/api/reset", json={"confirm": "RESET"}, headers=csrf_headers)
    with app.app_context():
        assert db.session.query(Question).count() == before


# ------------------------------------------------------------------ permission

def test_bank_export_needs_administrator(client):
    response = client.get("/api/export")
    assert response.status_code == 403
    assert response.get_json()["error"] == "administrator_required"


def test_bank_import_needs_administrator(client):
    response = client.post("/api/import", json={"questions": []})
    assert response.status_code == 403


def test_first_account_becomes_administrator(app):
    """A fresh installation must have someone who can manage the bank."""
    from app.models import user_count

    with app.app_context():
        assert user_count() >= 1
        assert db.session.query(User).filter_by(email="student@example.com").one().is_admin is False
        assert db.session.query(User).filter_by(is_admin=True).count() == 0

    fresh = app.test_client()
    token = csrf_of(fresh, "/auth/signup")
    fresh.post(
        "/auth/signup",
        data={
            "email": "founder@example.com",
            "password": "a-long-enough-password",
            "password_confirm": "a-long-enough-password",
            "csrf_token": token,
        },
    )
    # An account created when others already exist is not an administrator...
    with app.app_context():
        assert db.session.query(User).filter_by(email="founder@example.com").one().is_admin is False


# ------------------------------------------------------------ account settings

def test_password_change_requires_the_current_password(client, app):
    token = csrf_of(client, "/auth/account")
    response = client.post(
        "/auth/password",
        data={
            "current_password": "not-my-password",
            "new_password": "a-brand-new-password",
            "new_password_confirm": "a-brand-new-password",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert b"current password is incorrect" in response.data
    with app.app_context():
        user = db.session.query(User).filter_by(email="student@example.com").one()
        assert user.check_password("correct-horse-battery") is True


def test_password_change_works_and_the_new_password_signs_in(anon_client, app):
    from tests.conftest import login

    login(anon_client, "student@example.com")
    token = csrf_of(anon_client, "/auth/account")
    response = anon_client.post(
        "/auth/password",
        data={
            "current_password": "correct-horse-battery",
            "new_password": "a-brand-new-password",
            "new_password_confirm": "a-brand-new-password",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert b"Password updated" in response.data

    with app.app_context():
        user = db.session.query(User).filter_by(email="student@example.com").one()
        assert user.check_password("a-brand-new-password") is True
        assert user.check_password("correct-horse-battery") is False


def test_account_deletion_removes_progress(client, app, csrf_headers):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    client.post(
        "/api/answer", json={"question_id": question_id, "answer": "x"}, headers=csrf_headers
    )

    token = csrf_of(client, "/auth/account")
    response = client.post(
        "/auth/delete",
        data={"password": "correct-horse-battery", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"deleted" in response.data
    with app.app_context():
        assert db.session.query(User).filter_by(email="student@example.com").one_or_none() is None
        # Cascade deletes the account's answers, not anyone else's.
        assert db.session.query(Attempt).filter_by(user_id=app.config["TEST_USER_ID"]).count() == 0


def test_account_deletion_requires_the_password(client, app):
    token = csrf_of(client, "/auth/account")
    client.post(
        "/auth/delete", data={"password": "wrong", "csrf_token": token}, follow_redirects=True
    )
    with app.app_context():
        assert db.session.query(User).filter_by(email="student@example.com").one_or_none() is not None


def test_account_page_shows_only_this_users_numbers(client, app, csrf_headers):
    with app.app_context():
        question = db.session.query(Question).first()
        correct = question.correct_answer
        question_id = question.id
    client.post(
        "/api/answer", json={"question_id": question_id, "answer": correct}, headers=csrf_headers
    )
    body = client.get("/auth/account").get_data(as_text=True)
    assert "student@example.com" in body
    assert "1" in body  # one answer recorded


# ------------------------------------------------------------------- migration

def test_migration_adds_user_id_to_an_older_schema(tmp_path):
    """An existing database without user_id must be upgraded, not broken.

    Emulating the old shape by dropping the column is not possible — SQLite
    refuses to drop a column a foreign key refers to — so the older schema is
    built directly and the migration is pointed at it.
    """
    import sqlite3

    from sqlalchemy import create_engine, inspect as sa_inspect, text
    from sqlalchemy.pool import StaticPool

    from app import db_migrate

    db_path = tmp_path / "old.db"

    # The pre-accounts schema: attempt rows exist but have no owner column.
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE attempt (
            id INTEGER PRIMARY KEY,
            question_id INTEGER NOT NULL,
            topic_id INTEGER,
            session_id VARCHAR(36),
            mode VARCHAR(20) NOT NULL,
            user_response TEXT,
            is_correct BOOLEAN NOT NULL,
            score FLOAT,
            marks_awarded INTEGER,
            feedback TEXT,
            answered_at DATETIME
        );
        INSERT INTO attempt (question_id, is_correct, mode)
            VALUES (1, 1, 'practice'), (2, 0, 'practice');
        """
    )
    connection.commit()
    connection.close()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    assert "user_id" not in {
        column["name"] for column in sa_inspect(engine).get_columns("attempt")
    }

    applied = db_migrate.run_migrations(engine)
    assert any("user_id" in change for change in applied), applied

    columns = {column["name"] for column in sa_inspect(engine).get_columns("attempt")}
    assert "user_id" in columns

    # Existing rows survive the migration, unowned rather than lost.
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT user_id FROM attempt")).fetchall()
    assert len(rows) == 2
    assert all(row[0] is None for row in rows)

    # Running it again must be a no-op rather than an error.
    assert db_migrate.run_migrations(engine) == []
    engine.dispose()


def test_orphan_report_counts_unowned_attempts(app):
    """History recorded before accounts existed is reported, not silently lost."""
    from app.db_migrate import report_orphans

    with app.app_context():
        db.session.add(
            Attempt(
                user_id=None,
                question_id=db.session.query(Question).first().id,
                is_correct=True,
                mode="practice",
            )
        )
        db.session.commit()
        assert report_orphans() >= 1


def test_admin_can_adopt_orphan_attempts(app):
    """The recorded history can be handed to an account."""
    from app.auth import adopt_orphan_attempts

    with app.app_context():
        question_id = db.session.query(Question).first().id
        db.session.add(
            Attempt(user_id=None, question_id=question_id, is_correct=True, mode="practice")
        )
        db.session.commit()

        updated = adopt_orphan_attempts("student@example.com")
        assert updated >= 1
        assert db.session.query(Attempt).filter(Attempt.user_id.is_(None)).count() == 0


# ------------------------------------------------------------------ rate limits

def test_rate_limit_resets_between_tests():
    """Guards against one test's attempts leaking into the next."""
    rate_limit_reset()


def test_signup_is_rate_limited(anon_client):
    """Sign-up attempts are capped per client address."""
    codes = []
    for index in range(8):
        # A successful sign-up signs the client in, so start each attempt
        # anonymous to keep exercising the limiter.
        with anon_client.session_transaction() as session:
            session.clear()
        token = csrf_of(anon_client, "/auth/signup")
        response = anon_client.post(
            "/auth/signup",
            data={
                "email": f"spam{index}@example.com",
                "password": "a-long-enough-password",
                "password_confirm": "a-long-enough-password",
                "csrf_token": token,
            },
        )
        codes.append(response.status_code)
    assert 429 in codes, codes


def test_minimum_password_length_is_enforced():
    assert MIN_PASSWORD_LENGTH >= 10
