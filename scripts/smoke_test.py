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

        print("\n--- data I/O ---")
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
        response = client.post("/api/reset", json={}, headers=headers)
        check("reset needs confirmation", response.status_code == 400)

        response = client.post("/api/reset", json={"confirm": "RESET"}, headers=headers)
        check("reset deletes attempts", response.get_json()["deleted"] >= 5, str(response.get_json()))

        with app.app_context():
            check("no attempts remain", db.session.query(Attempt).count() == 0)

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
