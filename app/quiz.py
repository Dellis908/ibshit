"""Quiz assembly and session statistics."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import func

from app.extensions import db
from app.models import Attempt, Question, Topic

#: Supported quiz modes.
QUIZ_MODES = ("practice", "timed", "exam", "weak", "unseen", "random")

MODE_LABELS = {
    "practice": "Practice",
    "timed": "Timed",
    "exam": "Exam simulation",
    "weak": "Focus on weak topics",
    "unseen": "Never attempted",
    "random": "Random mix",
}


@dataclass
class QuizRequest:
    """A parsed request for a set of questions."""

    mode: str = "practice"
    topics: tuple[str, ...] = ()
    count: int = 10
    difficulty: str | None = None
    timed: bool = False
    seconds_per_mark: float = 60.0


def parse_quiz_request(args, defaults=None) -> QuizRequest:
    """Build a :class:`QuizRequest` from a Flask request args mapping."""
    defaults = defaults or {}
    default_count = int(defaults.get("count", 10))

    mode = str(args.get("mode") or "practice").strip().lower()
    if mode not in QUIZ_MODES:
        mode = "practice"

    topics = tuple(t for t in args.getlist("topic") if t) if hasattr(args, "getlist") else ()
    if not topics:
        single = args.get("topic")
        topics = (single,) if single else ()

    try:
        count = int(args.get("count") or default_count)
    except (TypeError, ValueError):
        count = default_count
    count = max(1, count)

    difficulty = (args.get("difficulty") or "").strip().lower() or None
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = None

    return QuizRequest(
        mode=mode,
        topics=topics,
        count=count,
        difficulty=difficulty,
        timed=mode == "timed" or str(args.get("timed") or "").lower() in {"1", "true", "yes"},
    )


def _base_query(topics: tuple[str, ...] = (), difficulty: str | None = None):
    query = db.session.query(Question).join(Topic)
    if topics:
        query = query.filter(Topic.slug.in_(list(topics)))
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    return query


def weak_topic_slugs(limit: int = 3, min_attempts: int = 3) -> list[str]:
    """Topics ordered by lowest accuracy (needs at least ``min_attempts``)."""
    rows = (
        db.session.query(
            Topic.slug,
            func.count(Attempt.id).label("attempts"),
            func.sum(db.case((Attempt.is_correct.is_(True), 1), else_=0)).label("correct"),
        )
        .join(Attempt, Attempt.topic_id == Topic.id)
        .group_by(Topic.slug)
        .having(func.count(Attempt.id) >= min_attempts)
        .all()
    )
    scored = []
    for slug, attempts, correct in rows:
        accuracy = (correct or 0) / attempts
        scored.append((accuracy, attempts, slug))
    scored.sort()
    return [slug for _accuracy, _attempts, slug in scored[:limit]]


def build_quiz(request: QuizRequest, *, max_count: int = 60, rng: random.Random | None = None) -> list[Question]:
    """Select the questions for a quiz request."""
    rng = rng or random.Random()
    count = max(1, min(request.count, max_count))
    topics = request.topics

    if request.mode == "weak" and not topics:
        weak = weak_topic_slugs()
        if weak:
            topics = tuple(weak)

    query = _base_query(topics, request.difficulty)

    if request.mode == "unseen":
        attempted = db.session.query(Attempt.question_id).distinct()
        query = query.filter(~Question.id.in_(attempted))

    if request.mode == "weak" and topics:
        # Weight towards questions the user has previously got wrong.
        wrong_ids = [
            row[0]
            for row in db.session.query(Attempt.question_id)
            .filter(Attempt.is_correct.is_(False))
            .distinct()
            .all()
        ]
        if wrong_ids:
            query = query.filter(Question.id.in_(wrong_ids))

    pool = query.all()
    if not pool:
        return []

    if request.mode == "unseen" or len(pool) <= count:
        rng.shuffle(pool)
        return pool[:count]

    # Prefer a balanced spread across topics, then pad randomly.
    by_topic: dict[str, list[Question]] = {}
    for question in pool:
        by_topic.setdefault(question.topic_slug or "unknown", []).append(question)
    for bucket in by_topic.values():
        rng.shuffle(bucket)

    selected: list[Question] = []
    topic_keys = list(by_topic)
    rng.shuffle(topic_keys)
    while len(selected) < count and any(by_topic.values()):
        for key in topic_keys:
            if len(selected) >= count:
                break
            if by_topic[key]:
                selected.append(by_topic[key].pop())
    return selected


def session_summary(session_id: str) -> dict:
    """Aggregate a quiz run's attempts into a results summary."""
    attempts = (
        db.session.query(Attempt)
        .filter(Attempt.session_id == session_id)
        .order_by(Attempt.answered_at, Attempt.id)
        .all()
    )
    total = len(attempts)
    correct = sum(1 for a in attempts if a.is_correct)
    marks_awarded = sum(a.marks_awarded or 0 for a in attempts)
    marks_available = sum((a.question.marks if a.question else 0) for a in attempts)

    per_topic: dict[str, dict] = {}
    for attempt in attempts:
        slug = attempt.topic.slug if attempt.topic else "unknown"
        bucket = per_topic.setdefault(
            slug,
            {
                "topic": slug,
                "title": attempt.topic.title if attempt.topic else slug,
                "attempts": 0,
                "correct": 0,
            },
        )
        bucket["attempts"] += 1
        if attempt.is_correct:
            bucket["correct"] += 1
    for bucket in per_topic.values():
        bucket["accuracy"] = (
            round(100.0 * bucket["correct"] / bucket["attempts"], 1) if bucket["attempts"] else 0.0
        )

    return {
        "session_id": session_id,
        "total": total,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy": round(100.0 * correct / total, 1) if total else None,
        "marks_awarded": marks_awarded,
        "marks_available": marks_available,
        "percentage": round(100.0 * marks_awarded / marks_available, 1) if marks_available else None,
        "per_topic": sorted(per_topic.values(), key=lambda b: b["accuracy"]),
        "attempts": [a.as_dict() for a in attempts],
    }


def topic_progress() -> list[dict]:
    """Per-topic progress: questions available, attempted, and accuracy."""
    totals = {
        slug: count
        for slug, count in db.session.query(Topic.slug, func.count(Question.id))
        .outerjoin(Question, Question.topic_id == Topic.id)
        .group_by(Topic.slug)
        .all()
    }
    attempted = {
        slug: count
        for slug, count in db.session.query(Topic.slug, func.count(func.distinct(Attempt.question_id)))
        .join(Attempt, Attempt.topic_id == Topic.id)
        .group_by(Topic.slug)
        .all()
    }
    accuracy = {
        slug: (correct or 0, total)
        for slug, correct, total in db.session.query(
            Topic.slug,
            func.sum(db.case((Attempt.is_correct.is_(True), 1), else_=0)),
            func.count(Attempt.id),
        )
        .join(Attempt, Attempt.topic_id == Topic.id)
        .group_by(Topic.slug)
        .all()
    }

    topics = db.session.query(Topic).order_by(Topic.position, Topic.title).all()
    progress = []
    for topic in topics:
        correct, total = accuracy.get(topic.slug, (0, 0))
        progress.append(
            {
                "slug": topic.slug,
                "title": topic.title,
                "short_title": topic.short_title,
                "syllabus_ref": topic.syllabus_ref,
                "question_count": totals.get(topic.slug, 0),
                "attempted": attempted.get(topic.slug, 0),
                "attempts": total,
                "correct": correct,
                "accuracy": round(100.0 * correct / total, 1) if total else None,
                "coverage": round(
                    100.0 * attempted.get(topic.slug, 0) / totals[topic.slug], 1
                )
                if totals.get(topic.slug)
                else 0.0,
            }
        )
    return progress


def recent_attempts(limit: int = 20) -> list[Attempt]:
    return (
        db.session.query(Attempt)
        .order_by(Attempt.answered_at.desc(), Attempt.id.desc())
        .limit(limit)
        .all()
    )
