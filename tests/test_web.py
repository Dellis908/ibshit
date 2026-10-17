"""Tests for the web pages and the JSON API."""

from __future__ import annotations

import json
import re


from app.extensions import db
from app.models import Attempt, Question


# ---------------------------------------------------------------------- pages

def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Biology" in body
    assert "Start a 10-question quiz" in body


def test_topics_page_lists_loaded_topics(client):
    response = client.get("/topics")
    assert response.status_code == 200
    assert "Cell Biology" in response.get_data(as_text=True)


def test_topics_page_groups_the_syllabus(client):
    """The page reads as themes A–D, then the 2024 block — not one flat list."""
    body = client.get("/topics").get_data(as_text=True)
    for heading in (
        "A — Unity and Diversity",
        "B — Form and Function",
        "C — Interaction and Interdependence",
        "D — Continuity and Change",
        "IB Biology (2024 programme)",
    ):
        assert heading in body, heading

    # A numbered reference appears before any 2024 topic, so the order is the
    # guide's, not the database's insertion order.
    assert body.index("A1.1") < body.index("IB Biology (2024 programme)")
    # The old flat checklist with its invented references is gone.
    assert "Syllabus checklist" not in body
    assert "badge-soft" not in body


def test_topic_detail_page(client, app):
    with app.app_context():
        question = db.session.query(Question).first()
        slug = question.topic.slug
    response = client.get(f"/topics/{slug}")
    assert response.status_code == 200
    assert question.topic.title in response.get_data(as_text=True)


def test_unknown_topic_redirects_with_flash(client):
    response = client.get("/topics/nope", follow_redirects=True)
    assert response.status_code == 200
    assert "No questions available" in response.get_data(as_text=True)


def test_progress_and_about_pages(client):
    assert client.get("/progress").status_code == 200
    assert client.get("/about").status_code == 200


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_404_page_for_html_requests(client):
    response = client.get("/definitely-not-a-page")
    assert response.status_code == 404
    assert "Page not found" in response.get_data(as_text=True)


def test_404_json_for_api_requests(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"


# ----------------------------------------------------------------- quiz pages

def test_quiz_page_renders_questions(client):
    response = client.get("/quiz?mode=practice&count=3")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("question-card") >= 3
    assert "csrf-token" in body


def test_quiz_page_hides_answers(client):
    """The mark scheme must never be sent to the browser before answering."""
    response = client.get("/quiz?count=5")
    body = response.get_data(as_text=True)
    assert "Mark scheme" not in body
    assert "correct_answer" not in body


def test_quiz_respects_requested_count(client):
    body = client.get("/quiz?count=2").get_data(as_text=True)
    assert body.count('class="question-card"') == 2


def test_quiz_filters_by_difficulty(client):
    body = client.get("/quiz?count=20&difficulty=easy").get_data(as_text=True)
    assert "badge-easy" in body
    assert "badge-hard" not in body


def test_quiz_never_serves_a_question_without_an_answer(client, app):
    """An unmarkable question must not reach a learner.

    The imported bank still contains two rows whose answer lived in a figure
    that was never published — imported before validation caught this. Serving
    one scores every attempt wrong, so the quiz query filters them out.
    """
    from app.extensions import db
    from app.models import Question, Topic
    from app.quiz import QuizRequest, build_quiz

    with app.app_context():
        # Inserted directly: validation now refuses such a payload, but the row
        # is already in production, so the query has to protect the quiz itself.
        topic = Topic.query.filter_by(slug="cell-biology").one()
        broken = Question(
            topic_id=topic.id,
            question_text="A question whose answer was never published?",
            question_type="multiple_choice",
            correct_answer="",
            choices_json=json.dumps(["a", "b", "c", "d"]),
            marks=1,
            difficulty="easy",
            explanation="",
            tags_json="[]",
            source="test-unanswerable",
            fingerprint="test-unanswerable-fingerprint",
        )
        db.session.add(broken)
        db.session.commit()
        broken_id = broken.id

        assert Question.query.filter_by(source="test-unanswerable").count() == 1

        served = [q.id for q in build_quiz(QuizRequest(count=60), user=None)]
        assert served, "the bank should still return questions"
        assert broken_id not in served


def test_quiz_by_topic_redirects_to_query_form(client):
    response = client.get("/quiz/cell-biology")
    assert response.status_code == 302
    assert "topic=cell-biology" in response.headers["Location"]


def test_quiz_with_unknown_topic_redirects(client):
    response = client.get("/quiz?topic=does-not-exist")
    assert response.status_code == 302


def test_timed_quiz_shows_a_timer(client):
    body = client.get("/quiz?mode=timed&count=2").get_data(as_text=True)
    assert "quiz-timer" in body
    assert "Time left" in body


def test_exam_mode_hides_immediate_reveal_notice(client):
    body = client.get("/quiz?mode=exam&count=2").get_data(as_text=True)
    assert "not revealed until you finish" in body


# ------------------------------------------------------------------ the API

def test_topics_endpoint(client):
    payload = client.get("/api/topics").get_json()
    topics = payload["topics"]
    slugs = {topic["slug"] for topic in topics}
    assert "cell-biology" in slugs
    # Every catalogue topic is listed, so gaps are visible; the ones with
    # questions carry a count.
    assert any(topic["question_count"] >= 1 for topic in topics)
    assert all("question_count" in topic for topic in topics)


def test_topics_endpoint_lists_the_syllabus_in_order(client):
    """The API order is the order the topics page renders: subject, then syllabus."""
    topics = client.get("/api/topics").get_json()["topics"]
    subjects = [topic.get("subject", "biology") for topic in topics]
    assert subjects == sorted(subjects) or subjects[0] == "biology", subjects[:5]

    biology = [t for t in topics if t.get("subject", "biology") == "biology"]
    assert biology[0]["syllabus_ref"] == "A1.1"
    numbered = [t["syllabus_ref"] for t in biology if t.get("syllabus_ref") and "." in t["syllabus_ref"]]
    assert numbered == sorted(numbered, key=lambda ref: (
        ref[0], int(re.match(r"[A-Z](\d+)", ref).group(1)), int(ref.split(".")[1])
    )), numbered[:8]

    chemistry = [t for t in topics if t.get("subject") == "chemistry"]
    assert chemistry, "the chemistry catalogue should be listed"
    assert chemistry[0]["syllabus_ref"].startswith("Structure 1.1")

    # The 2024 topics follow, and carry no invented syllabus reference.
    legacy = [t for t in topics if t["slug"] == "cell-biology"]
    assert legacy and not legacy[0].get("syllabus_ref")


def test_questions_endpoint_paginates_and_hides_answers(client):
    payload = client.get("/api/questions?limit=2").get_json()
    assert len(payload["questions"]) == 2
    assert payload["total"] >= 5
    assert all("correct_answer" not in q for q in payload["questions"])


def test_questions_endpoint_can_include_answers(client):
    payload = client.get("/api/questions?limit=1&include_answers=true").get_json()
    assert "correct_answer" in payload["questions"][0]


def test_stats_endpoint(client):
    payload = client.get("/api/stats").get_json()
    assert payload["overall"]["total_questions"] >= 5
    assert isinstance(payload["topics"], list)


def test_export_endpoint_round_trips(admin_client):
    payload = admin_client.get("/api/export").get_json()
    assert payload["meta"]["count"] >= 5
    assert all("question_text" in q for q in payload["questions"])


# -------------------------------------------------------------- answer marking

def test_answer_requires_csrf(client, app):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    response = client.post("/api/answer", json={"question_id": question_id, "answer": "x"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "invalid_csrf_token"


def test_answer_rejects_bad_payloads(client, csrf_headers):
    assert client.post("/api/answer", json={}, headers=csrf_headers).status_code == 400
    assert (
        client.post("/api/answer", json={"question_id": "abc"}, headers=csrf_headers).status_code
        == 400
    )
    assert (
        client.post("/api/answer", json={"question_id": 999999}, headers=csrf_headers).status_code
        == 404
    )


def test_correct_answer_is_marked_correct_and_recorded(client, app, csrf_headers):
    with app.app_context():
        question = (
            db.session.query(Question).filter(Question.question_type == "multiple_choice").first()
        )
        question_id = question.id
        expected = question.correct_answer

    response = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": expected},
        headers=csrf_headers,
    )
    payload = response.get_json()
    assert payload["correct"] is True
    assert payload["marks_awarded"] == payload["marks_available"]

    with app.app_context():
        attempt = db.session.query(Attempt).filter_by(question_id=question_id).first()
        assert attempt is not None
        assert attempt.is_correct is True
        assert attempt.session_id


def test_wrong_answer_returns_the_mark_scheme(client, app, csrf_headers):
    with app.app_context():
        question = (
            db.session.query(Question).filter(Question.question_type == "multiple_choice").first()
        )
        question_id = question.id
        expected = question.correct_answer

    payload = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "definitely wrong"},
        headers=csrf_headers,
    ).get_json()
    assert payload["correct"] is False
    assert payload["correct_answer"] == expected
    assert payload["explanation"]


def test_timed_mode_withholds_the_answer_until_revealed(client, app, csrf_headers):
    with app.app_context():
        question = db.session.query(Question).first()
        question_id = question.id

    payload = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "anything", "mode": "timed"},
        headers=csrf_headers,
    ).get_json()
    assert payload["correct_answer"] is None

    revealed = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "anything", "mode": "timed", "reveal": True},
        headers=csrf_headers,
    ).get_json()
    assert revealed["correct_answer"]


def test_result_and_results_page(client, app, csrf_headers):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    client.post(
        "/api/answer", json={"question_id": question_id, "answer": "x"}, headers=csrf_headers
    )
    summary = client.get("/api/result").get_json()
    assert summary["total"] >= 1
    assert client.get("/results").status_code == 200


def test_result_for_unknown_session_is_404(client):
    assert client.get("/api/result?session_id=missing").status_code == 404


def test_self_marking_override(client, app, csrf_headers):
    with app.app_context():
        question = (
            db.session.query(Question)
            .filter(Question.question_type == "short_answer", Question.marks >= 2)
            .first()
        )
        question_id = question.id
        available = question.marks

    payload = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "partial", "self_mark": True},
        headers=csrf_headers,
    ).get_json()
    attempt_id = payload["attempt_id"]

    updated = client.post(
        f"/api/attempts/{attempt_id}/override",
        json={"marks_awarded": available},
        headers=csrf_headers,
    ).get_json()
    assert updated["marks_awarded"] == available
    assert updated["is_correct"] is True

    # Out-of-range values are clamped.
    clamped = client.post(
        f"/api/attempts/{attempt_id}/override",
        json={"marks_awarded": 99},
        headers=csrf_headers,
    ).get_json()
    assert clamped["marks_awarded"] == available


def test_override_validates_input(client, csrf_headers):
    assert (
        client.post(
            "/api/attempts/999/override", json={"marks_awarded": 1}, headers=csrf_headers
        ).status_code
        == 404
    )


# ------------------------------------------------------------------- data I/O

def test_import_rejects_invalid_questions(admin_client):
    response = admin_client.post("/api/import", json={"questions": [{"question_text": "x"}]})
    payload = response.get_json()
    assert payload["created"] == 0
    assert payload["rejected"] == 1


def test_import_dry_run_writes_nothing(admin_client, app):
    with app.app_context():
        before = db.session.query(Question).count()

    response = admin_client.post(
        "/api/import?dry_run=true",
        json={
            "questions": [
                {
                    "topic": "genetics",
                    "type": "short_answer",
                    "question_text": "State one function of the Golgi apparatus in a cell.",
                    "correct_answer": "Modifies and packages proteins",
                    "marks": 1,
                    "explanation": "It processes proteins made in the rough ER.",
                    "difficulty": "easy",
                }
            ]
        },
    )
    assert response.get_json()["created"] == 1

    with app.app_context():
        assert db.session.query(Question).count() == before


def test_import_then_export_round_trip(admin_client, app):
    payload = {
        "questions": [
            {
                "topic": "evolution-biodiversity",
                "type": "true_false",
                "question_text": "Antibiotic resistance can evolve by natural selection.",
                "correct_answer": "True",
                "marks": 1,
                "explanation": "Resistant bacteria survive and reproduce.",
                "difficulty": "easy",
                "tags": ["selection"],
            }
        ]
    }
    created = admin_client.post("/api/import", json=payload).get_json()
    assert created["created"] == 1

    exported = admin_client.get("/api/export?topic=evolution-biodiversity").get_json()
    texts = [q["question_text"] for q in exported["questions"]]
    assert any("Antibiotic resistance" in text for text in texts)


def test_import_is_idempotent(admin_client, app):
    """Importing the same payload twice must not duplicate questions."""
    payload = {
        "questions": [
            {
                "topic": "ecology",
                "type": "short_answer",
                "question_text": "State what is meant by a trophic level in a food chain.",
                "correct_answer": "A feeding position in a food chain",
                "marks": 1,
                "explanation": "Producers occupy the first trophic level.",
                "difficulty": "easy",
            }
        ]
    }
    first = admin_client.post("/api/import", json=payload).get_json()
    second = admin_client.post("/api/import", json=payload).get_json()
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped"] == 1


def test_import_rejects_bad_json_shapes(admin_client):
    assert admin_client.post("/api/import", data="not json").status_code == 400
    assert admin_client.post("/api/import", json={"questions": "nope"}).status_code == 400


def test_api_key_gate_when_configured(admin_client, app):
    app.config["API_KEY"] = "secret-key"
    try:
        assert admin_client.get("/api/export").status_code == 401
        assert admin_client.get("/api/export?api_key=secret-key").status_code == 200
        assert (
            admin_client.get("/api/export", headers={"X-API-Key": "secret-key"}).status_code == 200
        )
    finally:
        app.config["API_KEY"] = None


# ----------------------------------------------------------------- resetting

def test_reset_requires_confirmation(client, csrf_headers):
    response = client.post("/api/reset", json={}, headers=csrf_headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "confirmation_required"


def test_reset_deletes_attempts(client, app, csrf_headers):
    with app.app_context():
        question_id = db.session.query(Question).first().id
    client.post(
        "/api/answer", json={"question_id": question_id, "answer": "x"}, headers=csrf_headers
    )

    payload = client.post(
        "/api/reset", json={"confirm": "RESET"}, headers=csrf_headers
    ).get_json()
    assert payload["deleted"] >= 1

    with app.app_context():
        assert db.session.query(Attempt).count() == 0
        # The question bank must be untouched.
        assert db.session.query(Question).count() >= 5


def test_reset_for_one_topic_only(client, app, csrf_headers):
    with app.app_context():
        questions = db.session.query(Question).limit(2).all()
        ids = [q.id for q in questions]
        slug = questions[0].topic.slug

    for question_id in ids:
        client.post(
            "/api/answer",
            json={"question_id": question_id, "answer": "x"},
            headers=csrf_headers,
        )

    payload = client.post(
        "/api/reset", json={"confirm": "RESET", "topic": slug}, headers=csrf_headers
    ).get_json()
    assert payload["deleted"] >= 1
    with app.app_context():
        remaining = db.session.query(Attempt).count()
        assert remaining >= 1


# ------------------------------------------------------------------- security

def test_question_content_is_sanitised_on_import(admin_client, app):
    payload = {
        "questions": [
            {
                "topic": "cell-biology",
                "type": "short_answer",
                "question_text": (
                    "Safe <sub>2</sub> text<script>alert(1)</script>"
                    "<img src=x onerror=alert(1)>"
                ),
                "correct_answer": "answer",
                "marks": 1,
                "explanation": "why",
            }
        ]
    }
    assert admin_client.post("/api/import", json=payload).get_json()["created"] == 1

    with app.app_context():
        stored = db.session.query(Question).order_by(Question.id.desc()).first().question_text
    assert "<sub>2</sub>" in stored
    assert "<script" not in stored.lower()
    assert "onerror" not in stored.lower()


def test_user_answers_are_escaped_in_results(client, app, csrf_headers):
    """Submitted prose must be escaped when rendered back to the page."""
    with app.app_context():
        question_id = db.session.query(Question).first().id

    client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "<script>alert('xss')</script>"},
        headers=csrf_headers,
    )
    body = client.get("/results").get_data(as_text=True)
    assert "<script>alert('xss')</script>" not in body
    assert "&lt;script&gt;" in body


def test_oversized_answer_is_truncated(client, app, csrf_headers):
    with app.app_context():
        question_id = db.session.query(Question).first().id

    response = client.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "x" * 20000},
        headers=csrf_headers,
    )
    assert response.status_code == 200

    with app.app_context():
        attempt = db.session.query(Attempt).order_by(Attempt.id.desc()).first()
        assert len(attempt.user_response) <= 5000
