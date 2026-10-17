"""Tests for second-opinion grading (no network: the transport is stubbed)."""

from __future__ import annotations

import pytest

from app import ai_grading
from app.extensions import db
from app.models import Question, Topic


@pytest.fixture
def written_question(app):
    """A three-mark question whose mark scheme is prose, like the chemistry bank."""
    with app.app_context():
        topic = db.session.query(Topic).filter_by(slug="chem-s22-the-covalent-model").one_or_none()
        if topic is None:
            topic = Topic(slug="chem-s22-the-covalent-model", title="Covalent model", subject="chemistry", position=0)
            db.session.add(topic)
            db.session.flush()
        question = Question(
            topic_id=topic.id,
            question_text="Explain why ammonia has a higher boiling point than methane.",
            question_type="short_answer",
            correct_answer=(
                "Award [1] for hydrogen bonds between ammonia molecules; "
                "Award [1] for more energy needed to separate them; "
                "Award [1] for methane only has weak dispersion forces."
            ),
            marks=3,
            difficulty="medium",
            choices_json="[]",
            tags_json="[]",
            source="ai-test",
            fingerprint="ai-test-fingerprint",
        )
        db.session.add(question)
        db.session.commit()
        return question.id


def _config(**overrides):
    config = {
        "AI_GRADING_ENABLED": True,
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_MODEL": "test/model:free",
        "AI_GRADING_TIMEOUT": 2,
        "PREFERRED_URL_SCHEME": "https",
        "SERVER_NAME": "localhost",
    }
    config.update(overrides)
    return config


def _reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_disabled_by_default(app, written_question):
    """No key, no call — a fresh install must never talk to the network."""
    with app.app_context():
        question = db.session.get(Question, written_question)
        verdict = type("V", (), {"marks_awarded": 1, "marks_available": 3})()
        assert not ai_grading.should_consult(question, verdict, "hydrogen bonds", app.config)
        assert ai_grading.grade(question, "hydrogen bonds", app.config) is None


def test_one_mark_and_perfect_answers_are_not_consulted(app, written_question):
    with app.app_context():
        question = db.session.get(Question, written_question)

        one_mark = type("V", (), {"marks_awarded": 0, "marks_available": 1})()
        assert not ai_grading.should_consult(question, one_mark, "x", _config())

        perfect = type("V", (), {"marks_awarded": 3, "marks_available": 3})()
        assert not ai_grading.should_consult(question, perfect, "x", _config())

        partial = type("V", (), {"marks_awarded": 1, "marks_available": 3})()
        assert ai_grading.should_consult(question, partial, "hydrogen bonds", _config())
        assert not ai_grading.should_consult(question, partial, "   ", _config())


def test_model_marks_are_applied_and_clamped(app, written_question, monkeypatch):
    replies = iter([_reply('{"marks": 2, "awarded_points": ["hydrogen bonds"], "missed_points": ["energy"], "feedback": "Mostly right."}'),
                    _reply('{"marks": 99, "feedback": "too generous"}')])

    def fake_post(url, body, headers, timeout):
        assert headers["Authorization"] == "Bearer test-key"
        return next(replies)

    monkeypatch.setattr(ai_grading, "_post_json", fake_post)
    ai_grading.reset_cache()

    with app.app_context():
        question = db.session.get(Question, written_question)
        verdict = ai_grading.grade(question, "ammonia has hydrogen bonds", _config())
        assert verdict is not None
        assert verdict.marks == 2
        assert verdict.feedback == "Mostly right."
        assert verdict.awarded_points == ["hydrogen bonds"]

        # The model never exceeds the marks the question is worth.
        generous = ai_grading.grade(question, "a different answer", _config())
        assert generous is not None and generous.marks == 3


def test_failure_falls_back_to_the_deterministic_result(app, written_question, monkeypatch):
    def boom(url, body, headers, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(ai_grading, "_post_json", boom)
    ai_grading.reset_cache()
    with app.app_context():
        question = db.session.get(Question, written_question)
        assert ai_grading.grade(question, "anything", _config()) is None


def test_unparseable_reply_is_ignored(app, written_question, monkeypatch):
    monkeypatch.setattr(ai_grading, "_post_json", lambda *a, **k: _reply("I think probably 2 marks?"))
    ai_grading.reset_cache()
    with app.app_context():
        question = db.session.get(Question, written_question)
        assert ai_grading.grade(question, "anything", _config()) is None


def test_identical_answers_are_only_sent_once(app, written_question, monkeypatch):
    calls = []

    def fake_post(url, body, headers, timeout):
        calls.append(body)
        return _reply('{"marks": 1, "feedback": "partial"}')

    monkeypatch.setattr(ai_grading, "_post_json", fake_post)
    ai_grading.reset_cache()
    with app.app_context():
        question = db.session.get(Question, written_question)
        first = ai_grading.grade(question, "Hydrogen  bonds", _config())
        second = ai_grading.grade(question, "hydrogen bonds", _config())
        assert first and second and first.marks == second.marks
        assert len(calls) == 1, "the second identical answer should come from the cache"


def test_endpoint_uses_the_second_opinion(client, app, csrf_headers, written_question, monkeypatch):
    """The API returns the model's marks, not the engine's, for a written answer."""
    monkeypatch.setattr(
        ai_grading,
        "_post_json",
        lambda *a, **k: _reply('{"marks": 3, "feedback": "Full marks from the examiner."}'),
    )
    ai_grading.reset_cache()
    app.config.update(_config())

    response = client.post(
        "/api/answer",
        json={"question_id": written_question, "answer": "ammonia forms hydrogen bonds", "mode": "practice"},
        headers=csrf_headers,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ai_graded"] is True
    assert body["marks_awarded"] == 3
    assert body["ai_feedback"] == "Full marks from the examiner."
    assert body["correct"] is True
