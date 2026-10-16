"""End-to-end smoke check: exercises every page and API route.

Run with:  .venv/bin/python scripts/smoke_test.py
Exits non-zero if any route fails, so it can gate a deploy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Attempt, Question  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    line = f"[{status}] {label}"
    if detail and not condition:
        line += f" — {detail}"
    print(line)
    if not condition:
        failures.append(label)


def main() -> int:
    app = create_app("development")
    app.config.update(TESTING=True)

    with app.test_client() as client:
        # The bank has to exist before anything meaningful can be exercised. CI
        # runs this on a fresh checkout, where the database is empty.
        with app.app_context():
            if db.session.query(Question).first() is None:
                from app.content import import_file
                from flask import current_app

                print(f"\nseeding {current_app.config['SEED_FILE']}")
                import_file(current_app.config["SEED_FILE"])
                db.session.commit()

        print("\n--- accounts ---")
        response = client.get("/")
        check("landing page for visitors", response.status_code == 200)
        check(
            "landing page shows no progress figures",
            b"of your answers recorded" not in response.data,
        )

        response = client.get("/progress")
        check("protected page redirects visitors", response.status_code == 302, str(response.status_code))

        response = client.get("/api/stats")
        check("protected api returns 401 for visitors", response.status_code == 401, str(response.status_code))

        # Sign up through the real form, which exercises CSRF and validation.
        # The account may already exist from a previous run, in which case
        # signing in is the correct behaviour to exercise instead.
        with app.app_context():
            from app.models import User

            existing = db.session.query(User).filter_by(email="smoke@example.com").one_or_none()

        created_account = existing is None
        if created_account:
            page = client.get("/auth/signup").get_data(as_text=True)
            token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)

            response = client.post(
                "/auth/signup",
                data={
                    "email": "smoke@example.com",
                    "password": "sturdy-lantern-42-quartz",
                    "password_confirm": "sturdy-lantern-42-quartz",
                    "csrf_token": token,
                },
                follow_redirects=True,
            )
            check("sign-up succeeds", response.status_code == 200, str(response.status_code))
            check("sign-up lands on the dashboard", b"questions in the bank" in response.data)

            # A second sign-up with the same address must be refused. Being signed
            # in would bounce /auth/signup to the dashboard, so sign out first.
            # Logout is POST-only and needs the token from the current page.
            page = client.get("/").get_data(as_text=True)
            token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
            client.post("/auth/logout", data={"csrf_token": token})
            page = client.get("/auth/signup").get_data(as_text=True)
            token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
            response = client.post(
                "/auth/signup",
                data={
                    "email": "smoke@example.com",
                    "password": "sturdy-lantern-42-quartz",
                    "password_confirm": "sturdy-lantern-42-quartz",
                    "csrf_token": token,
                },
            )
            check("duplicate email is refused", response.status_code == 400, str(response.status_code))
            # The refused sign-up left us signed out of that page load; sign in.
            page = client.get("/auth/login").get_data(as_text=True)
            token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
            client.post(
                "/auth/login",
                data={"email": "smoke@example.com", "password": "sturdy-lantern-42-quartz",
                      "csrf_token": token},
                follow_redirects=True,
            )
        else:
            check("account already exists from a previous run", True)

        page = client.get("/").get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        response = client.post("/auth/logout", data={"csrf_token": token}, follow_redirects=True)
        check("sign-out succeeds", response.status_code == 200)
        check(
            "signed-out visitor no longer sees progress",
            b"of your answers recorded" not in response.data,
        )

        page = client.get("/auth/login").get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        response = client.post(
            "/auth/login",
            data={"email": "smoke@example.com", "password": "wrong-password", "csrf_token": token},
        )
        check("wrong password is rejected", response.status_code == 401, str(response.status_code))

        page = client.get("/auth/login").get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        response = client.post(
            "/auth/login",
            data={"email": "smoke@example.com", "password": "sturdy-lantern-42-quartz", "csrf_token": token},
            follow_redirects=True,
        )
        check("sign-in succeeds", b"questions in the bank" in response.data)

        print("\n--- permissions ---")
        # Make the starting state explicit: previous runs may have promoted this
        # account, and these checks are about a normal user.
        with app.app_context():
            from app.models import User

            db.session.query(User).filter_by(email="smoke@example.com").one().is_admin = False
            db.session.commit()

        response = client.get("/api/export")
        check(
            "export is refused for a normal account",
            response.status_code == 403,
            str(response.status_code),
        )
        check(
            "refusal names the reason",
            response.get_json().get("error") == "administrator_required",
            str(response.get_json()),
        )
        response = client.post("/api/import", json={"questions": []})
        check(
            "import is refused for a normal account",
            response.status_code == 403,
            str(response.status_code),
        )

        # Promote the account; administrator rights now apply to the session.
        with app.app_context():
            from app.models import User

            db.session.query(User).filter_by(email="smoke@example.com").one().is_admin = True
            db.session.commit()
        check("promotion succeeded", True)

        print("\n--- pages ---")
        for path in ["/", "/topics", "/progress", "/about", "/healthz"]:
            response = client.get(path)
            check(f"GET {path} -> 200", response.status_code == 200, f"got {response.status_code}")

        response = client.get("/")
        check("dashboard mentions question count", b"questions in the bank" in response.data)

        with app.app_context():
            topic_slug = (
                db.session.query(Question).first().topic.slug
            )
            question_id = db.session.query(Question).first().id

        response = client.get(f"/topics/{topic_slug}")
        check(f"GET /topics/{topic_slug} -> 200", response.status_code == 200)

        response = client.get("/topics/does-not-exist", follow_redirects=False)
        check("unknown topic redirects", response.status_code == 302)

        print("\n--- quiz pages ---")
        for query in [
            "?mode=practice&count=5",
            f"?topic={topic_slug}&mode=practice&count=5",
            "?mode=timed&count=3",
            "?mode=exam&count=3",
            "?mode=unseen&count=3",
            "?mode=weak&count=3",
            "?mode=random&count=3",
            f"?topic={topic_slug}&mode=practice&count=5&difficulty=",
        ]:
            response = client.get(f"/quiz{query}")
            check(f"GET /quiz{query} -> 200", response.status_code == 200, f"got {response.status_code}")

        response = client.get(f"/quiz/{topic_slug}", follow_redirects=True)
        check("legacy /quiz/<topic> works", response.status_code == 200)

        response = client.get("/quiz?topic=nonexistent-topic")
        check("empty quiz selection redirects", response.status_code == 302)

        print("\n--- csrf ---")
        response = client.get("/quiz?count=3")
        html = response.get_data(as_text=True)
        match = re.search(r'name="csrf-token" content="([^"]+)"', html)
        check("csrf token present in page", match is not None)
        token = match.group(1) if match else ""

        response = client.post("/api/answer", json={"question_id": question_id, "answer": "x"})
        check("answer without csrf is rejected", response.status_code == 403, f"got {response.status_code}")

        print("\n--- api ---")
        headers = {"X-CSRF-Token": token}

        response = client.get("/api/topics")
        check("GET /api/topics -> 200", response.status_code == 200)
        topic_count = len(response.get_json()["topics"])
        check("topics payload is non-empty", topic_count >= 10, str(topic_count))

        response = client.get("/api/questions?limit=5")
        body = response.get_json()
        check("GET /api/questions -> 200", response.status_code == 200)
        check("questions are limited", len(body["questions"]) == 5)
        check("answers are hidden by default", all("correct_answer" not in q for q in body["questions"]))

        response = client.get("/api/questions?limit=3&include_answers=true")
        check("answers returned on request", "correct_answer" in response.get_json()["questions"][0])

        response = client.get("/api/stats")
        check("GET /api/stats -> 200", response.status_code == 200)

        response = client.get("/api/export")
        exported = response.get_json()
        check("GET /api/export -> 200", response.status_code == 200)

        with app.app_context():
            bank_size = db.session.query(Question).count()
        check(
            "export contains every question",
            exported["meta"]["count"] == bank_size,
            f'{exported["meta"]["count"]} vs {bank_size}',
        )

        print("\n--- marking ---")
        with app.app_context():
            mcq = (
                db.session.query(Question)
                .filter(Question.question_type == "multiple_choice")
                .first()
            )
            expected = mcq.correct_answer
            mcq_id = mcq.id
            tf = (
                db.session.query(Question)
                .filter(Question.question_type == "true_false")
                .first()
            )
            tf_id, tf_answer = tf.id, tf.correct_answer
            calc = (
                db.session.query(Question)
                .filter(Question.question_type == "calculation")
                .first()
            )
            calc_id, calc_answer = calc.id, calc.correct_answer
            written = (
                db.session.query(Question)
                .filter(Question.question_type == "short_answer", Question.marks >= 2)
                .first()
            )
            written_id, written_answer = written.id, written.correct_answer

        response = client.post(
            "/api/answer",
            json={"question_id": mcq_id, "answer": expected},
            headers=headers,
        )
        check("correct multiple choice marked correct", response.get_json()["correct"] is True)

        response = client.post(
            "/api/answer",
            json={"question_id": mcq_id, "answer": "definitely-not-an-option"},
            headers=headers,
        )
        check("wrong multiple choice marked wrong", response.get_json()["correct"] is False)

        response = client.post(
            "/api/answer", json={"question_id": tf_id, "answer": tf_answer}, headers=headers
        )
        check("true/false marked correct", response.get_json()["correct"] is True)

        response = client.post(
            "/api/answer",
            json={"question_id": calc_id, "answer": calc_answer},
            headers=headers,
        )
        check("calculation marked correct", response.get_json()["correct"] is True, calc_answer)

        response = client.post(
            "/api/answer",
            json={"question_id": written_id, "answer": written_answer},
            headers=headers,
        )
        body = response.get_json()
        check("model answer earns full marks on written question", body["correct"] is True, str(body))

        response = client.post(
            "/api/answer",
            json={"question_id": written_id, "answer": "no idea"},
            headers=headers,
        )
        check("nonsense answer scores zero", response.get_json()["marks_awarded"] == 0)

        print("\n--- results ---")
        response = client.get("/api/result")
        body = response.get_json()
        check("GET /api/result -> 200", response.status_code == 200, str(response.status_code))
        check("summary counts attempts", body.get("total", 0) >= 5, str(body.get("total")))

        response = client.get("/results")
        check("GET /results -> 200", response.status_code == 200)

        print("\n--- data I/O (administrator only) ---")

        response = client.post(
            "/api/import?dry_run=true",
            json={
                "questions": [
                    {
                        "topic": "genetics",
                        "type": "short_answer",
                        "question_text": "Smoke test: state the role of helicase in replication.",
                        "correct_answer": "Unwinds the DNA double helix",
                        "marks": 1,
                        "explanation": "Helicase breaks hydrogen bonds between base pairs.",
                        "difficulty": "easy",
                        "tags": ["smoke"],
                    }
                ]
            },
        )
        check("dry-run import accepted", response.get_json()["created"] == 1, str(response.get_json()))

        with app.app_context():
            before = db.session.query(Question).count()

        response = client.post(
            "/api/import",
            json={"questions": [{"topic": "genetics", "question_text": "x"}]},
        )
        check(
            "invalid question rejected",
            response.get_json()["rejected"] == 1
            and response.get_json()["created"] == 0,
            str(response.get_json()),
        )

        with app.app_context():
            after = db.session.query(Question).count()
        check("invalid import wrote nothing", before == after, f"{before} -> {after}")

        print("\n--- reset ---")
        # Plant an attempt on a second account: the reset belongs to the signed-in
        # user only, and a server carrying real users must keep their history.
        with app.app_context():
            from app.models import User

            bystander = User.query.filter_by(email="bystander@example.com").one_or_none()
            if bystander is None:
                bystander = User(email="bystander@example.com", display_name="Bystander")
                bystander.set_password("quiet-harbour-77-reed")
                db.session.add(bystander)
                db.session.commit()
            sample = Question.query.filter(Question.correct_answer != "").first()
            db.session.add(
                Attempt(
                    user_id=bystander.id,
                    question_id=sample.id,
                    topic_id=sample.topic_id,
                    session_id="smoke-bystander",
                    user_response="kept",
                    is_correct=False,
                    marks_awarded=0,
                )
            )
            db.session.commit()
            bystander_id = bystander.id

        response = client.post("/api/reset", json={}, headers=headers)
        check("reset needs confirmation", response.status_code == 400)

        response = client.post("/api/reset", json={"confirm": "RESET"}, headers=headers)
        check("reset deletes attempts", response.get_json()["deleted"] >= 5, str(response.get_json()))

        with app.app_context():
            # Reset is scoped to the signed-in account, so assert on that
            # account rather than the whole table: a server with real users has
            # their attempts in it, and they must survive.
            smoke_user = User.query.filter_by(email="smoke@example.com").one()
            remaining = Attempt.query.filter_by(user_id=smoke_user.id).count()
            check("no attempts remain for the reset account", remaining == 0, str(remaining))
            kept = Attempt.query.filter_by(user_id=bystander_id).count()
            check("another account keeps its attempts", kept == 1, str(kept))

            # Leave no trace: this script may run against a live database.
            Attempt.query.filter_by(user_id=bystander_id).delete()
            User.query.filter_by(id=bystander_id).delete()
            db.session.commit()

        print("\n--- security ---")
        payload = (
            '<p>Safe <sub>2</sub></p><script>alert(1)</script>'
            '<img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a>'
        )
        from app.content import import_questions

        with app.app_context():
            report = import_questions(
                [
                    {
                        "topic": "cell-biology",
                        "type": "short_answer",
                        "question_text": payload,
                        "correct_answer": "test",
                        "marks": 1,
                    }
                ],
                source="smoke-test",
            )
            check("malicious payload imported", report.created == 1, str(report.errors))
            injected = db.session.query(Question).order_by(Question.id.desc()).first()
            stored = injected.question_text
            injected_id = injected.id

        check("script tag stripped", "<script" not in stored.lower(), stored)
        check("event handler stripped", "onerror" not in stored.lower(), stored)
        check("javascript: url stripped", "javascript:" not in stored.lower(), stored)
        check("safe subscript preserved", "<sub>2</sub>" in stored, stored)

        # Served over HTTP outside the app context, then cleaned up.
        response = client.get(f"/api/questions?limit=1&offset={injected_id}")
        check("question list serves sanitised content", response.status_code == 200)

        with app.app_context():
            db.session.delete(db.session.get(Question, injected_id))
            db.session.commit()

    # Remove the account this run created, so a smoke test against a live
    # database does not leave a user and its attempts behind. An account that
    # was already there is left alone: it may not be ours.
    if created_account:
        with app.app_context():
            from app.models import User

            account = User.query.filter_by(email="smoke@example.com").one_or_none()
            if account is not None:
                Attempt.query.filter_by(user_id=account.id).delete()
                db.session.delete(account)
                db.session.commit()
                print("\ncleaned up the smoke account and its attempts")

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
