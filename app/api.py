"""JSON API.

Endpoints used by the quiz front-end plus a few automation-friendly routes:

======================  ==========================================
``GET  /api/topics``    Topic list with progress
``GET  /api/questions`` Filter the question bank
``GET  /api/stats``     Overall and per-topic statistics
``POST /api/answer``    Submit one answer and get it marked
``GET  /api/result``    Summary of the current (or given) quiz run
``POST /api/reset``     Delete recorded attempts
``GET  /api/export``    Export the question bank as JSON
``POST /api/import``    Import questions from JSON
======================  ==========================================

State-changing routes require a CSRF token (``X-CSRF-Token`` header or a
``csrf_token`` field), issued to the page in a meta tag.
"""

from __future__ import annotations

import hmac
import secrets

from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user

from app import grading
from app.content import export_questions, import_questions
from app.extensions import db
from app.models import DIFFICULTIES, QUESTION_TYPES, Attempt, Question, Topic, overall_stats
from app.quiz import MODE_LABELS, QUIZ_MODES, session_summary, topic_progress
from app.routes import current_session_id
from app.security import admin_required, login_required_api

api = Blueprint("api", __name__, url_prefix="/api")


# ------------------------------------------------------------------- security

def csrf_token() -> str:
    """Return this session's CSRF token, creating it on first use."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _csrf_ok() -> bool:
    expected = session.get("csrf_token")
    if not expected:
        return False
    supplied = request.headers.get("X-CSRF-Token") or (
        request.get_json(silent=True) or {}
    ).get("csrf_token")
    return bool(supplied) and hmac.compare_digest(str(supplied), str(expected))


def _api_key_ok() -> bool:
    """Import/export are open locally and key-protected once API_KEY is set."""
    configured = current_app.config.get("API_KEY")
    if not configured:
        return True
    supplied = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    return bool(supplied) and hmac.compare_digest(str(supplied), str(configured))


def _guard_write():
    """Return an error response when a write request fails its checks."""
    if not _csrf_ok():
        return jsonify({"error": "invalid_csrf_token"}), 403
    return None


# --------------------------------------------------------------------- topics

@api.get("/topics")
@login_required_api
def list_topics():
    progress = {row["slug"]: row for row in topic_progress(current_user)}
    topics = db.session.query(Topic).order_by(Topic.subject, Topic.position, Topic.title).all()
    payload = []
    for topic in topics:
        data = topic.as_dict()
        row = progress.get(topic.slug)
        if row:
            data.update(
                {
                    "attempted": row["attempted"],
                    "accuracy": row["accuracy"],
                    "coverage": row["coverage"],
                }
            )
        payload.append(data)
    return jsonify({"topics": payload, "modes": MODE_LABELS})


@api.get("/questions")
@login_required_api
def list_questions():
    """Filter the bank. Answers are only included on explicit request."""
    query = db.session.query(Question).join(Topic)
    slug = request.args.get("topic")
    if slug:
        query = query.filter(Topic.slug == slug)
    difficulty = request.args.get("difficulty")
    if difficulty in DIFFICULTIES:
        query = query.filter(Question.difficulty == difficulty)
    qtype = request.args.get("type")
    if qtype in QUESTION_TYPES:
        query = query.filter(Question.question_type == qtype)

    try:
        limit = min(int(request.args.get("limit", 50)), 500)
    except (TypeError, ValueError):
        limit = 50
    offset = max(0, int(request.args.get("offset", 0) or 0))

    total = query.count()
    questions = query.order_by(Question.id).offset(offset).limit(limit).all()
    include_answers = str(request.args.get("include_answers", "")).lower() in {"1", "true", "yes"}

    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "questions": [
                q.as_dict(include_answer=include_answers) for q in questions
            ],
        }
    )


# ---------------------------------------------------------------------- stats

@api.get("/stats")
@login_required_api
def stats():
    return jsonify(
        {
            "overall": overall_stats(current_user),
            "topics": topic_progress(current_user),
            "modes": MODE_LABELS,
        }
    )


@api.get("/result")
@login_required_api
def result():
    sid = request.args.get("session_id") or session.get("quiz_session")
    if not sid:
        return jsonify({"error": "no_active_quiz"}), 404
    summary = session_summary(sid, current_user)
    if not summary["total"]:
        return jsonify({"error": "no_attempts_for_session"}), 404
    return jsonify(summary)


# --------------------------------------------------------------------- answer

@api.post("/answer")
@login_required_api
def submit_answer():
    """Mark one response and record it as an attempt."""
    guard = _guard_write()
    if guard:
        return guard

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id") or payload.get("id")
    response = payload.get("answer")
    if response is None:
        response = payload.get("response", "")

    if question_id is None:
        return jsonify({"error": "question_id_required"}), 400
    try:
        question_id = int(question_id)
    except (TypeError, ValueError):
        return jsonify({"error": "question_id_must_be_an_integer"}), 400

    question = db.session.get(Question, question_id)
    if question is None:
        return jsonify({"error": "question_not_found"}), 404

    # Guard against oversized junk input.
    response = str(response)[:5000]

    verdict = grading.grade(
        question,
        response,
        fuzzy_threshold=current_app.config.get("FUZZY_THRESHOLD", 72),
        tolerance=current_app.config.get("NUMERIC_TOLERANCE", 0.01),
    )

    mode = str(payload.get("mode") or "practice")
    if mode not in QUIZ_MODES:
        mode = "practice"

    attempt = Attempt(
        user_id=current_user.id,
        question_id=question.id,
        topic_id=question.topic_id,
        session_id=payload.get("session_id") or current_session_id(),
        mode=mode,
        user_response=response,
        is_correct=verdict.correct,
        score=verdict.score,
        marks_awarded=verdict.marks_awarded,
        feedback=verdict.feedback,
    )
    db.session.add(attempt)
    db.session.commit()

    body = {
        "attempt_id": attempt.id,
        "question_id": question.id,
        "correct": verdict.correct,
        "verdict": verdict.verdict,
        "score": round(verdict.score, 1),
        "marks_awarded": verdict.marks_awarded,
        "marks_available": verdict.marks_available,
        "feedback": verdict.feedback,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "keywords_matched": verdict.keywords_matched,
        "keywords_missed": verdict.keywords_missed,
    }

    # Let the client self-mark longer written answers against the mark scheme.
    if (
        str(payload.get("self_mark", "")).lower() in {"1", "true", "yes"}
        and question.question_type == "short_answer"
    ):
        body["self_mark_available"] = True

    # Everything except answers from a timed/exam run gets immediate feedback.
    if mode in {"timed", "exam"} and not payload.get("reveal"):
        body["correct_answer"] = None
        body["explanation"] = None

    return jsonify(body)


@api.post("/submit_answer")
@api.post("/legacy/answer")
@login_required_api
def legacy_submit_answer():
    """The original ``POST /submit_answer`` contract, kept for compatibility.

    The first version of this app exposed ``/submit_answer`` returning
    ``{is_correct, correct_answer}`` and a ``UserAnswer`` table. Any existing
    client or bookmark keeps working through this endpoint; it records a normal
    :class:`~app.models.Attempt` so progress stays in one place.
    """
    guard = _guard_write()
    if guard:
        return guard

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    answer = str(payload.get("answer") or "")[:5000]

    try:
        question_id = int(question_id)
    except (TypeError, ValueError):
        return jsonify({"error": "question_id_required"}), 400

    question = db.session.get(Question, question_id)
    if question is None:
        return jsonify({"error": "question_not_found"}), 404

    verdict = grading.grade(
        question,
        answer,
        fuzzy_threshold=current_app.config.get("FUZZY_THRESHOLD", 72),
        tolerance=current_app.config.get("NUMERIC_TOLERANCE", 0.01),
    )

    db.session.add(
        Attempt(
            user_id=current_user.id,
            question_id=question.id,
            topic_id=question.topic_id,
            session_id=current_session_id(),
            mode="practice",
            user_response=answer,
            is_correct=verdict.correct,
            score=verdict.score,
            marks_awarded=verdict.marks_awarded,
            feedback=verdict.feedback,
        )
    )
    db.session.commit()

    # The original response shape, including the old key name.
    return jsonify(
        {
            "is_correct": verdict.correct,
            "correct_answer": question.correct_answer,
            "feedback": verdict.feedback,
            "explanation": question.explanation,
        }
    )


@api.post("/attempts/<int:attempt_id>/override")
@login_required_api
def override_attempt(attempt_id: int):
    """Apply a user's self-marking decision to a written answer."""
    guard = _guard_write()
    if guard:
        return guard

    payload = request.get_json(silent=True) or {}
    awarded = payload.get("marks_awarded")
    attempt = db.session.get(Attempt, attempt_id)
    if attempt is None or attempt.user_id != current_user.id:
        # Not found and not yours are deliberately the same answer, so the
        # response cannot be used to probe which attempt ids exist.
        return jsonify({"error": "attempt_not_found"}), 404
    try:
        awarded = int(awarded)
    except (TypeError, ValueError):
        return jsonify({"error": "marks_awarded_must_be_an_integer"}), 400

    available = attempt.question.marks if attempt.question else 1
    attempt.marks_awarded = max(0, min(awarded, available))
    attempt.is_correct = attempt.marks_awarded >= available
    attempt.feedback = "Self-marked."
    db.session.commit()
    return jsonify(attempt.as_dict())


# ---------------------------------------------------------------------- reset

@api.post("/reset")
@login_required_api
def reset_attempts():
    """Delete recorded attempts (all, or just one topic)."""
    guard = _guard_write()
    if guard:
        return guard

    payload = request.get_json(silent=True) or {}
    if str(payload.get("confirm") or "").strip().upper() != "RESET":
        return jsonify({"error": "confirmation_required", "hint": "send confirm=RESET"}), 400

    # Scoped to the caller: nobody can clear another account's history.
    query = db.session.query(Attempt).filter(Attempt.user_id == current_user.id)
    slug = payload.get("topic")
    if slug:
        topic = db.session.query(Topic).filter_by(slug=slug).one_or_none()
        if topic is None:
            return jsonify({"error": "topic_not_found"}), 404
        query = query.filter(Attempt.topic_id == topic.id)

    deleted = query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"deleted": deleted, "topic": slug})


# ------------------------------------------------------------------- data I/O

@api.get("/export")
@admin_required
def export_bank():
    if not _api_key_ok():
        return jsonify({"error": "invalid_api_key"}), 401
    slug = request.args.get("topic")
    with_stats = str(request.args.get("stats", "")).lower() in {"1", "true", "yes"}
    payload = export_questions(topic_slug=slug, include_stats=with_stats)
    response = jsonify(payload)
    response.headers["Content-Disposition"] = "attachment; filename=questions.json"
    return response


@api.post("/import")
@admin_required
def import_bank():
    """Import questions from a JSON body: either a payload or a raw list."""
    if not _api_key_ok():
        return jsonify({"error": "invalid_api_key"}), 401

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "expected_json_body"}), 400

    if isinstance(body, dict):
        questions = body.get("questions") or body.get("items") or []
    elif isinstance(body, list):
        questions = body
    else:
        return jsonify({"error": "unsupported_json_root"}), 400

    if not isinstance(questions, list):
        return jsonify({"error": "questions_must_be_a_list"}), 400
    if len(questions) > 5000:
        return jsonify({"error": "too_many_questions", "limit": 5000}), 400

    dry_run = str(request.args.get("dry_run", "")).lower() in {"1", "true", "yes"}
    report = import_questions(
        questions,
        source="api",
        default_topic=request.args.get("topic"),
        dry_run=dry_run,
    )
    return jsonify(
        {
            "dry_run": dry_run,
            "created": report.created,
            "skipped": report.skipped,
            "rejected": report.rejected,
            "errors": report.errors[:50],
            "per_topic": report.per_topic,
        }
    )

# ---------------------------------------------------------------- image proxy

#: Content types we are willing to mirror, and a hard size ceiling.
IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/svg+xml"
}
MAX_IMAGE_BYTES = 6 * 1024 * 1024


def _image_cache_dir() -> Path:
    directory = Path(current_app.config["BASE_DIR"]) / "instance" / "images"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _is_allowed_image_url(url: str) -> bool:
    """Whether an image URL may be fetched.

    Live hosts must be on the allow-list. URLs pointing at a retired host are
    also accepted so they can be rewritten onto a live one, which recovers
    images the bank recorded before the last migration.
    """
    from urllib.parse import urlparse

    from app.images import is_dead_host, is_supported_host

    if urlparse(url).scheme != "https":
        return False
    return is_supported_host(url) or is_dead_host(url)


@api.get("/image")
def proxy_image():
    """Serve a question image, mirroring it locally on first use.

    Question banks host their diagrams on object storage that has been migrated
    more than once, so an image recorded under one host may still exist under a
    legacy one. Every candidate URL is tried before giving up (see app.images).
    Proxying also keeps quizzes working when storage is slow or unreachable,
    avoids leaking the reader's IP to a third party, and lets the browser cache
    images. Only hosts on the allow-list are fetched, so this cannot be used as
    an open proxy.
    """
    import hashlib
    import mimetypes
    import urllib.error
    import urllib.request

    from flask import Response, abort

    from app.images import candidates, explain

    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url_required"}), 400
    if not _is_allowed_image_url(url):
        return jsonify({"error": "image_host_not_allowed", "host": urlparse(url).hostname}), 403

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}:
        suffix = ".img"
    cached = _image_cache_dir() / f"{digest}{suffix}"

    content_type = None
    if cached.exists() and cached.stat().st_size > 0:
        payload = cached.read_bytes()
        content_type = mimetypes.guess_type(str(cached))[0]
    else:
        payload = None
        for candidate in candidates(url):
            upstream = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ib-biology-revision/1.0)",
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
            try:
                with urllib.request.urlopen(upstream, timeout=15) as response:
                    detected = (
                        (response.headers.get("Content-Type") or "").split(";")[0].strip()
                    )
                    if detected and detected not in IMAGE_CONTENT_TYPES:
                        continue
                    body = response.read(MAX_IMAGE_BYTES + 1)
            except Exception:
                # A dead mirror is expected; move on to the next candidate.
                continue

            if len(body) > MAX_IMAGE_BYTES:
                abort(413, "image is too large to mirror")
            payload = body
            content_type = detected or None
            break

        if payload is None:
            # 404 is the honest answer when the figure has been deleted upstream,
            # and it tells the client to show its "unavailable" placeholder.
            # The reason is returned in the body so the state is diagnosable
            # rather than a bare error page.
            return (
                jsonify(
                    {
                        "error": "image_unavailable",
                        "reason": explain(url),
                        "url": url,
                    }
                ),
                404,
            )

        try:
            cached.write_bytes(payload)
        except OSError:  # pragma: no cover - read-only filesystem
            pass

    response = Response(payload, mimetype=content_type or "application/octet-stream")
    response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return response
