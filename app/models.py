"""Database models for the IB Biology quiz."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

QUESTION_TYPES = ("multiple_choice", "short_answer", "true_false", "calculation")
DIFFICULTIES = ("easy", "medium", "hard")


def utcnow() -> datetime:
    """Timezone-aware UTC now (``datetime.utcnow`` is deprecated)."""
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    """An account. Progress belongs to a user, never to the installation.

    Passwords are stored only as Werkzeug hashes (scrypt by default), so a
    database leak does not expose credentials.
    """

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(80))
    password_hash = db.Column(db.String(255), nullable=False)
    #: The first account created becomes an administrator: it may import or
    #: export the question bank. Everyone else can only study.
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True))

    attempts = db.relationship(
        "Attempt", back_populates="user", cascade="all, delete-orphan"
    )

    # --------------------------------------------------------------- password
    def set_password(self, password: str) -> None:
        """Hash and store a password. Never stores the plaintext."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Whether ``password`` matches this account's hash."""
        if not self.password_hash or not password:
            return False
        return check_password_hash(self.password_hash, password)

    # ------------------------------------------------------------------ misc
    @property
    def name(self) -> str:
        """What to show in the interface."""
        return self.display_name or self.email.split("@")[0]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "is_admin": self.is_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id: str):
    """Flask-Login hook: resolve the session's user id to a User row."""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


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
    #: Owner of this answer. Progress is per account.
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), index=True, nullable=True
    )
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
    user = db.relationship("User", back_populates="attempts")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
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


def overall_stats(user=None) -> dict:
    """Statistics for one user.

    ``user`` may be a :class:`User`, a user id, or ``None`` for an
    unauthenticated visitor. Progress figures are always scoped to that user, so
    one account can never see another's history.
    """
    user_id = getattr(user, "id", user)

    question_query = db.session.query(func.count(Question.id))
    attempt_query = db.session.query(func.count(Attempt.id))
    correct_query = db.session.query(func.count(Attempt.id)).filter(
        Attempt.is_correct.is_(True)
    )
    seen_query = db.session.query(func.count(func.distinct(Attempt.question_id)))

    if user_id is None:
        # No account: report the bank size only, never another user's progress.
        attempt_query = attempt_query.filter(db.false())
        correct_query = correct_query.filter(db.false())
        seen_query = seen_query.filter(db.false())
    else:
        attempt_query = attempt_query.filter(Attempt.user_id == user_id)
        correct_query = correct_query.filter(Attempt.user_id == user_id)
        seen_query = seen_query.filter(Attempt.user_id == user_id)

    total_questions = question_query.scalar() or 0
    total_attempts = attempt_query.scalar() or 0
    correct_attempts = correct_query.scalar() or 0
    seen = seen_query.scalar() or 0

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


def user_count() -> int:
    """How many accounts exist. Used to decide whether the next is an admin."""
    return db.session.query(func.count(User.id)).scalar() or 0
