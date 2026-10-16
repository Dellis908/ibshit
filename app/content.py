"""Importing and exporting question content.

Both the seed file and the scraper funnel through :func:`import_questions` so
that validation, sanitising, de-duplication and topic creation all happen in
exactly one place.

The expected JSON shape (also produced by ``flask quiz export``)::

    {
      "meta": {"syllabus": "...", "version": 1},
      "questions": [
        {
          "topic": "cell-biology",
          "type": "multiple_choice",
          "question_text": "Which organelle ...?",
          "correct_answer": "Mitochondrion",
          "choices": ["Mitochondrion", "Ribosome", "Golgi", "Nucleus"],
          "marks": 1,
          "explanation": "...",
          "tags": ["organelles"],
          "difficulty": "easy"
        }
      ]
    }

A JSON array of question objects, or JSON Lines, is accepted too.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.extensions import db
from app.models import DIFFICULTIES, QUESTION_TYPES, Question, Topic
from app.sanitize import sanitize_html, sanitize_plain, text_with_images, to_plain_text
from app.topics import (
    NEW_SYLLABUS_BY_SLUG,
    TOPIC_CATALOGUE,
    TOPICS_BY_SLUG,
    normalize_topic,
)

#: Characters that survive normalisation when fingerprinting question text.
_FINGERPRINT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------- results

@dataclass
class ImportReport:
    """Outcome of an import run."""

    created: int = 0
    skipped: int = 0
    topics_created: int = 0
    #: Questions that failed validation (not the number of error messages).
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    per_topic: dict[str, int] = field(default_factory=dict)

    @property
    def total_seen(self) -> int:
        return self.created + self.skipped + self.rejected

    def summary(self) -> str:
        lines = [
            f"created: {self.created}",
            f"skipped (duplicate): {self.skipped}",
            f"rejected (invalid questions): {self.rejected}",
            f"topics created: {self.topics_created}",
        ]
        if self.per_topic:
            lines.append("by topic:")
            for slug, count in sorted(self.per_topic.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append(f"  {slug}: {count}")
        if self.errors:
            lines.append(f"validation messages ({len(self.errors)}):")
            lines.extend(f"  - {e}" for e in self.errors[:10])
        return "\n".join(lines)


# ----------------------------------------------------------------------- topic

def sync_topics() -> int:
    """Ensure every catalogue topic exists in the database.

    Returns the number of topics created. Existing topics are refreshed from
    the catalogue so metadata changes in code take effect.
    """
    created = 0
    for position, spec in enumerate(TOPIC_CATALOGUE):
        topic = db.session.query(Topic).filter_by(slug=spec.slug).one_or_none()
        if topic is None:
            topic = Topic(slug=spec.slug)
            db.session.add(topic)
            created += 1
        topic.title = spec.title
        topic.short_title = spec.short_title
        topic.description = spec.description
        topic.syllabus_ref = spec.syllabus_ref
        topic.position = position

    # The 2025-syllabus subtopics are created up front as well, so imported
    # questions attach to a topic that already has its proper title.
    for offset, spec in enumerate(NEW_SYLLABUS_BY_SLUG.values()):
        existing = db.session.query(Topic).filter_by(slug=spec.slug).one_or_none()
        if existing is None:
            db.session.add(
                Topic(
                    slug=spec.slug,
                    title=spec.full_title,
                    short_title=spec.ref,
                    description=f"{spec.title} — {spec.theme}.",
                    syllabus_ref=spec.ref,
                    position=len(TOPIC_CATALOGUE) + offset,
                )
            )
            created += 1

    db.session.flush()
    return created


def _syllabus_topic(slug: str) -> tuple[Topic, bool] | None:
    """Return the 2025-syllabus topic for an exact catalogue slug.

    This runs before fuzzy matching: a slug such as ``bio-b32-transport``
    contains the word "transport", and keyword scoring would otherwise file it
    under the legacy "cell-biology" topic.
    """
    spec = NEW_SYLLABUS_BY_SLUG.get(slug)
    if spec is None:
        return None

    topic = db.session.query(Topic).filter_by(slug=spec.slug).one_or_none()
    if topic is None:
        position = len(TOPIC_CATALOGUE) + list(NEW_SYLLABUS_BY_SLUG).index(spec.slug)
        topic = Topic(
            slug=spec.slug,
            title=spec.full_title,
            short_title=spec.ref,
            description=f"{spec.title} — {spec.theme}.",
            syllabus_ref=spec.ref,
            position=position,
        )
        db.session.add(topic)
        db.session.flush()
        return topic, True

    # Keep catalogue metadata authoritative for existing rows.
    topic.title = spec.full_title
    topic.short_title = topic.short_title or spec.ref
    topic.syllabus_ref = spec.ref
    return topic, False


def get_or_create_topic(slug_or_text: str) -> tuple[Topic, bool]:
    """Resolve a topic slug, creating a database row if necessary.

    Unknown text is mapped onto a catalogue slug when possible; otherwise it is
    stored as an ad-hoc topic so that scraped material is never silently lost.
    """
    # Exact 2025-syllabus slugs win over fuzzy matching.
    exact = _syllabus_topic(str(slug_or_text or "").strip())
    if exact is not None:
        return exact

    slug = normalize_topic(slug_or_text)
    if slug:
        topic = db.session.query(Topic).filter_by(slug=slug).one_or_none()
        if topic is None:
            spec = TOPICS_BY_SLUG[slug]
            topic = Topic(
                slug=spec.slug,
                title=spec.title,
                short_title=spec.short_title,
                description=spec.description,
                syllabus_ref=spec.syllabus_ref,
                position=TOPIC_CATALOGUE.index(spec),
            )
            db.session.add(topic)
            db.session.flush()
            return topic, True
        return topic, False

    # Ad-hoc topic from free text.
    cleaned = sanitize_plain(slug_or_text, max_length=150) or "Uncategorised"
    adhoc_slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-") or "uncategorised"
    topic = db.session.query(Topic).filter_by(slug=adhoc_slug).one_or_none()
    if topic is None:
        position = db.session.query(db.func.count(Topic.id)).scalar() or 0
        topic = Topic(slug=adhoc_slug, title=cleaned, short_title=cleaned[:40], position=position)
        db.session.add(topic)
        db.session.flush()
        return topic, True
    return topic, False


# ----------------------------------------------------------------- fingerprint

def fingerprint(
    text: str,
    *,
    answer: str | None = None,
    choices: list[str] | None = None,
    marks: int | None = None,
) -> str:
    """A stable hash used to detect duplicate questions.

    The stem alone is not enough: two questions can share a stem while having
    different options or mark allocations (which happens in real banks), and
    treating those as duplicates would silently drop a legitimate question. The
    answer, the options and the mark allocation are therefore part of the hash.
    """
    parts = [_normalise_for_hash(text)]
    if answer is not None:
        parts.append(_normalise_for_hash(answer))
    if choices:
        parts.append("|".join(sorted(_normalise_for_hash(c) for c in choices)))
    if marks is not None:
        parts.append(str(marks))
    normalised = "\u0000".join(parts)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _normalise_for_hash(value: str | None) -> str:
    return _WS_RE.sub(" ", _FINGERPRINT_RE.sub(" ", (value or "").lower())).strip()


# ------------------------------------------------------------------ validation

def validate_question(raw: dict, index: int) -> list[str]:
    """Return a list of problems with a raw question dict (empty means valid)."""
    problems: list[str] = []
    label = f"question #{index + 1}"

    if not isinstance(raw, dict):
        return [f"{label}: not a JSON object"]

    text = to_plain_text(raw.get("question_text") or raw.get("question") or "")
    if len(text) < 5:
        problems.append(f"{label}: question_text is missing or too short")

    correct = text_with_images(raw.get("correct_answer") or raw.get("answer") or "")
    if not correct:
        problems.append(f"{label}: correct_answer is missing")

    qtype = str(raw.get("type") or raw.get("question_type") or "short_answer").strip().lower()
    if qtype not in QUESTION_TYPES:
        problems.append(f"{label}: unknown type {qtype!r}")

    difficulty = str(raw.get("difficulty") or "medium").strip().lower()
    if difficulty not in DIFFICULTIES:
        problems.append(f"{label}: unknown difficulty {difficulty!r}")

    try:
        marks = int(raw.get("marks", 1))
    except (TypeError, ValueError):
        problems.append(f"{label}: marks is not an integer")
        marks = 1
    if not 1 <= marks <= 20:
        problems.append(f"{label}: marks out of range (1-20)")

    if qtype == "multiple_choice":
        choices = raw.get("choices") or []
        if not isinstance(choices, list) or len(choices) < 2:
            problems.append(f"{label}: multiple_choice needs at least 2 choices")
        else:
            # Image-based options carry no words, so their URLs must survive the
            # comparison or four distinct images would look like duplicates.
            cleaned = [text_with_images(c) for c in choices]
            if len(set(cleaned)) != len(cleaned):
                problems.append(f"{label}: duplicate choices")
            if correct and correct not in cleaned:
                problems.append(f"{label}: correct_answer is not one of the choices")
    elif qtype == "true_false":
        if correct.lower() not in {"true", "false"}:
            problems.append(f"{label}: true_false answer must be True or False")

    return problems


# ----------------------------------------------------------------------- build

def build_question(raw: dict, *, topic_slug: str | None = None, source: str | None = None) -> dict:
    """Normalise one raw question dict into ORM-ready keyword arguments."""
    qtype = str(raw.get("type") or raw.get("question_type") or "short_answer").strip().lower()
    if qtype not in QUESTION_TYPES:
        qtype = "short_answer"

    difficulty = str(raw.get("difficulty") or "medium").strip().lower()
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"

    question_text = sanitize_html(raw.get("question_text") or raw.get("question") or "")
    correct_answer = sanitize_plain(raw.get("correct_answer") or raw.get("answer") or "")
    explanation = sanitize_plain(raw.get("explanation") or raw.get("rationale") or "")

    choices = []
    if qtype == "multiple_choice":
        choices = [sanitize_plain(c) for c in (raw.get("choices") or []) if to_plain_text(c)]
        # Keep the stored answer byte-identical to its choice so exact matching works.
        for choice in choices:
            if choice.lower() == correct_answer.lower():
                correct_answer = choice
                break

    tags = []
    raw_tags = raw.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t for t in re.split(r"[,\n]", raw_tags)]
    for tag in raw_tags:
        tag = sanitize_plain(tag, max_length=40).lower().strip()
        if tag and tag not in tags:
            tags.append(tag)

    try:
        marks = max(1, min(20, int(raw.get("marks", 1))))
    except (TypeError, ValueError):
        marks = 1

    return {
        "question_text": question_text,
        "question_type": qtype,
        "correct_answer": correct_answer,
        "choices_json": json.dumps(choices, ensure_ascii=False),
        "explanation": explanation,
        "marks": marks,
        "difficulty": difficulty,
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "image_path": sanitize_plain(raw.get("image_path") or "") or None,
        "source": source or sanitize_plain(raw.get("source") or "") or None,
        "fingerprint": fingerprint(
            question_text, answer=correct_answer, choices=choices, marks=marks
        ),
        "topic_slug": topic_slug or raw.get("topic"),
    }


# ---------------------------------------------------------------------- import

def load_payload(path: str | Path) -> tuple[list[dict], dict]:
    """Read a JSON or JSON Lines file into ``(questions, meta)``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"seed file not found: {path}")
    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return [], {}

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try JSON Lines.
        questions = []
        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                questions.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        return questions, {}

    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict):
        questions = payload.get("questions") or payload.get("items") or []
        if not isinstance(questions, list):
            raise ValueError("'questions' must be a list")
        return questions, payload.get("meta") or {}
    raise ValueError("unsupported JSON root: expected an object or a list")


def _question_source(raw: dict, file_source: str | None) -> str | None:
    """Pick the provenance label for one question.

    A question's own ``source`` wins: the importers tag every item they emit
    (``rdojo:``, ``village:``) and collapsing those onto the file name would
    throw away exactly the information needed to trace a question back.
    """
    own = str(raw.get("source") or "").strip()
    return own or file_source


def import_questions(
    raw_questions: list[dict],
    *,
    source: str = "import",
    default_topic: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Validate and insert questions, skipping duplicates.

    On ``dry_run`` the payload is validated and counted but nothing is written
    and no topics are created, so a dry run never touches the database.

    Commits on success; on failure the transaction is rolled back and the error
    is re-raised so the caller can report it.
    """
    report = ImportReport()
    existing = {row[0] for row in db.session.query(Question.fingerprint).all() if row[0]}

    valid: list[tuple[str, str | None, dict]] = []  # (fingerprint, topic, kwargs)

    for index, raw in enumerate(raw_questions):
        problems = validate_question(raw, index)
        if problems:
            report.rejected += 1
            report.errors.extend(problems)
            continue

        built = build_question(raw, source=_question_source(raw, source))
        fp = built["fingerprint"]
        if fp in existing:
            report.skipped += 1
            continue

        existing.add(fp)  # de-duplicates within this payload too
        topic_value = built.pop("topic_slug") or default_topic
        valid.append((fp, topic_value, built))

    report.created = len(valid)

    if dry_run:
        report.topics_created = 0
        for _fp, topic_value, _built in valid:
            slug = normalize_topic(topic_value) or (topic_value or "uncategorised")
            report.per_topic[slug] = report.per_topic.get(slug, 0) + 1
        return report

    try:
        for _fp, topic_value, built in valid:
            topic, created_topic = get_or_create_topic(topic_value or "")
            if created_topic:
                report.topics_created += 1
            db.session.add(Question(topic_id=topic.id, **built))
            report.per_topic[topic.slug] = report.per_topic.get(topic.slug, 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return report


def import_file(
    path: str | Path,
    *,
    dry_run: bool = False,
    default_topic: str | None = None,
) -> ImportReport:
    """Import questions from a JSON/JSONL file."""
    questions, _meta = load_payload(path)
    return import_questions(
        questions,
        source=f"file:{Path(path).name}",
        default_topic=default_topic,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------- export

def export_questions(
    *,
    topic_slug: str | None = None,
    include_answers: bool = True,
    include_stats: bool = False,
) -> dict:
    """Serialise the question bank back into the seed-file shape."""
    query = db.session.query(Question).join(Topic)
    if topic_slug:
        query = query.filter(Topic.slug == topic_slug)
    questions = query.order_by(Topic.position, Question.id).all()

    items: list[dict] = []
    for question in questions:
        item = {
            "topic": question.topic_slug,
            "type": question.question_type,
            "question_text": question.question_text,
            "marks": question.marks,
            "difficulty": question.difficulty,
            "tags": question.tags,
        }
        if include_answers:
            item["correct_answer"] = question.correct_answer
            item["choices"] = question.choices
            item["explanation"] = question.explanation
        if include_stats:
            item["stats"] = question.stats()
        items.append(item)

    return {
        "meta": {
            "syllabus": "IB Biology",
            "exported_from": "ibshit",
            "count": len(items),
            "topic": topic_slug,
        },
        "questions": items,
    }
