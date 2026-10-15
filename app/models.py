"""Database models for the IB Biology quiz."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db

QUESTION_TYPES = ("multiple_choice", "short_answer", "true_false", "calculation")
DIFFICULTIES = ("easy", "medium", "hard")


def utcnow() -> datetime:
    """Timezone-aware UTC now (``datetime.utcnow`` is deprecated)."""
    return datetime.now(timezone.utc)


class Topic(db.Model):
    """A syllabus topic that groups questions."""

    __tablename__ = "topic"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    short_title = db.Column(db.String(80))
    description = db.Column(db.Text)
    syllabus_ref = db.Column(db.String(40))
    position = db.Column(db.Integer, default=0, nullable=False)

    questions = db.relationship(
        "Question",
        back_populates="topic",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ------------------------------------------------------------------ utils
    @property
    def question_count(self) -> int:
        return len(self.questions)

    def total_marks(self) -> int:
        return sum(q.marks or 0 for q in self.questions)

    def as_dict(self, *, include_counts: bool = True) -> dict:
        data = {
            "slug": self.slug,
            "title": self.title,
            "short_title": self.short_title,
            "description": self.description,
            "syllabus_ref": self.syllabus_ref,
            "position": self.position,
        }
        if include_counts:
            data["question_count"] = self.question_count
            data["total_marks"] = self.total_marks()
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Topic {self.slug}>"


class Question(db.Model):
    """A single question with its mark scheme."""

    __tablename__ = "question"

    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True, nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(20), default="short_answer", nullable=False)
    correct_answer = db.Column(db.Text, nullable=False, default="")
    #: JSON-encoded list of options for multiple choice questions.
    choices_json = db.Column(db.Text, default="[]")
    explanation = db.Column(db.Text, default="")
    marks = db.Column(db.Integer, default=1, nullable=False)
    difficulty = db.Column(db.String(10), default="medium", nullable=False)
    tags_json = db.Column(db.Text, default="[]")
    image_path = db.Column(db.String(255))
    #: Provenance, e.g. "seed:v1" or the source URL for scraped questions.
    source = db.Column(db.String(500))
    #: Stable hash of the question text, used to avoid importing duplicates.
    fingerprint = db.Column(db.String(64), index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    topic = db.relationship("Topic", back_populates="questions")
    attempts = db.relationship(
        "Attempt",
        back_populates="question",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # ------------------------------------------------------------- properties
    @property
    def choices(self) -> list[str]:
        return _load_list(self.choices_json)

    @choices.setter
    def choices(self, value) -> None:
        self.choices_json = _dump_list(value)

    @property
    def tags(self) -> list[str]:
        return _load_list(self.tags_json)

    @tags.setter
    def tags(self, value) -> None:
        self.tags_json = _dump_list(value)

    @property
    def is_multiple_choice(self) -> bool:
        return self.question_type == "multiple_choice"

    @property
    def topic_slug(self) -> str | None:
        return self.topic.slug if self.topic else None

    def stats(self) -> dict:
        """Aggregate attempt statistics for this question."""
        total = self.attempts.count()
        correct = self.attempts.filter_by(is_correct=True).count()
        return {
            "attempts": total,
            "correct": correct,
            "accuracy": round(100.0 * correct / total, 1) if total else None,
        }

    def as_dict(self, *, include_answer: bool = False, include_stats: bool = False) -> dict:
        """Serialise the question.

        ``include_answer`` must stay ``False`` for anything sent to a client
        that has not answered yet, otherwise the answer leaks to the browser.
        """
        data = {
            "id": self.id,
            "topic": self.topic_slug,
            "type": self.question_type,
            "question_text": self.question_text,
            "marks": self.marks,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "image_path": self.image_path,
        }
        if self.is_multiple_choice:
            data["choices"] = self.choices
        if include_answer:
            data["correct_answer"] = self.correct_answer
            data["explanation"] = self.explanation
        if include_stats:
            data["stats"] = self.stats()
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Question {self.id} {self.question_type}>"


class Attempt(db.Model):
    """One answer submitted by a user for one question."""

    __tablename__ = "attempt"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(
        db.Integer, db.ForeignKey("question.id"), index=True, nullable=False
    )
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    #: Groups attempts into a single quiz run.
    session_id = db.Column(db.String(36), index=True)
    mode = db.Column(db.String(20), default="practice", nullable=False)
    user_response = db.Column(db.Text, default="")
    is_correct = db.Column(db.Boolean, default=False, nullable=False)
    #: Similarity score 0-100 for self-marked question types.
    score = db.Column(db.Float)
    marks_awarded = db.Column(db.Integer, default=0)
    feedback = db.Column(db.Text, default="")
    answered_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    question = db.relationship("Question", back_populates="attempts")
    topic = db.relationship("Topic")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "question_id": self.question_id,
            "topic": self.topic.slug if self.topic else None,
            "session_id": self.session_id,
            "mode": self.mode,
            "user_response": self.user_response,
            "is_correct": self.is_correct,
            "score": self.score,
            "marks_awarded": self.marks_awarded,
            "feedback": self.feedback,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Attempt {self.id} q={self.question_id} correct={self.is_correct}>"


# --------------------------------------------------------------------- helpers
def _load_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return value
    return []


def _dump_list(value) -> str:
    if not value:
        return "[]"
    if isinstance(value, str):
        value = [value]
    items = [str(v).strip() for v in value if str(v).strip()]
    return json.dumps(items, ensure_ascii=False)


def overall_stats() -> dict:
    """Site-wide statistics used by the dashboard."""
    total_questions = db.session.query(func.count(Question.id)).scalar() or 0
    total_attempts = db.session.query(func.count(Attempt.id)).scalar() or 0
    correct_attempts = (
        db.session.query(func.count(Attempt.id)).filter(Attempt.is_correct.is_(True)).scalar()
        or 0
    )
    # Unique questions the user has ever attempted.
    seen = db.session.query(func.count(func.distinct(Attempt.question_id))).scalar() or 0
    return {
        "total_questions": total_questions,
        "total_attempts": total_attempts,
        "correct_attempts": correct_attempts,
        "accuracy": round(100.0 * correct_attempts / total_attempts, 1)
        if total_attempts
        else None,
        "questions_seen": seen,
        "coverage": round(100.0 * seen / total_questions, 1) if total_questions else 0.0,
    }


# ------------------------------------------------------- backwards compatibility
# The first version of this app stored answers in a ``UserAnswer`` model and
# exposed ``POST /submit_answer``. Both are now provided by ``Attempt`` and
# ``/api/submit_answer``; this alias keeps older imports (and any scripts that
# referenced the model) working without a second table.
UserAnswer = Attempt
