"""Main web routes: dashboard, topics, quiz, results and progress."""

from __future__ import annotations

import uuid

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.extensions import db
from app.models import Question, Topic, overall_stats
from app.quiz import (
    MODE_LABELS,
    QUIZ_MODES,
    build_quiz,
    parse_quiz_request,
    recent_attempts,
    session_summary,
    topic_progress,
)
from app.topics import TOPIC_CATALOGUE, TOPICS_BY_SLUG

bp = Blueprint("main", __name__)


# --------------------------------------------------------------------- helpers

def current_session_id() -> str:
    """The active quiz-run id for this browser session, created on demand."""
    sid = session.get("quiz_session")
    if not sid:
        sid = str(uuid.uuid4())
        session["quiz_session"] = sid
    return sid


def start_new_session() -> str:
    """Begin a fresh quiz run so attempts are grouped per run."""
    sid = str(uuid.uuid4())
    session["quiz_session"] = sid
    return sid


def available_topics() -> list[Topic]:
    """Topics that actually contain questions, in syllabus order."""
    return (
        db.session.query(Topic)
        .join(Question, Question.topic_id == Topic.id)
        .order_by(Topic.position, Topic.title)
        .all()
    )


# ----------------------------------------------------------------------- pages

@bp.route("/")
def index():
    stats = overall_stats()
    progress = {row["slug"]: row for row in topic_progress()}
    return render_template(
        "index.html",
        stats=stats,
        topics=available_topics(),
        progress=progress,
        modes=MODE_LABELS,
    )


@bp.route("/topics")
def topics():
    progress = {row["slug"]: row for row in topic_progress()}
    return render_template(
        "topics.html",
        topics=available_topics(),
        progress=progress,
        catalogue=TOPIC_CATALOGUE,
    )


@bp.route("/topics/<slug>")
def topic_detail(slug: str):
    topic = db.session.query(Topic).filter_by(slug=slug).one_or_none()
    if topic is None or not topic.questions:
        flash(f"No questions available for '{slug}' yet.", "warning")
        return redirect(url_for("main.topics"))
    progress = {row["slug"]: row for row in topic_progress()}
    return render_template(
        "topic_detail.html",
        topic=topic,
        progress=progress.get(slug),
        modes=MODE_LABELS,
    )


@bp.route("/quiz")
def quiz():
    """Build and present a quiz from query parameters."""
    start_new_session()
    default_count = current_app.config.get("DEFAULT_QUESTION_COUNT", 10)
    max_count = current_app.config.get("MAX_QUESTION_COUNT", 60)

    req = parse_quiz_request(request.args, defaults={"count": default_count})
    req.count = max(1, min(req.count, max_count))

    questions = build_quiz(req, max_count=max_count)
    if not questions:
        flash(
            "No questions matched that selection. Try a different topic, mode or difficulty.",
            "warning",
        )
        return redirect(url_for("main.index"))

    topic_titles = {}
    for slug in req.topics:
        spec = TOPICS_BY_SLUG.get(slug)
        topic_titles[slug] = spec.title if spec else slug

    # Timed/exam runs get a server-enforced allowance of one minute per mark.
    time_limit = None
    if req.timed:
        seconds_per_mark = int(current_app.config.get("QUIZ_SECONDS_PER_MARK", 60))
        time_limit = int(sum(q.marks or 1 for q in questions) * seconds_per_mark)

    return render_template(
        "quiz.html",
        questions=questions,
        request_spec={
            "mode": req.mode,
            "topics": list(req.topics),
            "count": req.count,
            "difficulty": req.difficulty,
            "timed": req.timed,
        },
        topic_titles=topic_titles,
        time_limit=time_limit,
        mode_label=MODE_LABELS.get(req.mode, req.mode.title()),
    )


@bp.route("/quiz/<topic>")
def quiz_by_topic(topic: str):
    """Backwards-compatible shortcut for ``/quiz?topic=<topic>``."""
    args = request.args.to_dict(flat=False)
    args.setdefault("topic", [topic])
    args.setdefault("mode", ["practice"])
    query = "&".join(f"{key}={value}" for key, values in args.items() for value in values)
    return redirect(f"{url_for('main.quiz')}?{query}")


@bp.route("/results")
def results():
    sid = session.get("quiz_session")
    if not sid:
        flash("Finish a quiz to see your results.", "warning")
        return redirect(url_for("main.index"))
    summary = session_summary(sid)
    if not summary["total"]:
        flash("No answers recorded for the last quiz.", "warning")
        return redirect(url_for("main.index"))
    return render_template("results.html", summary=summary)


@bp.route("/results/<session_id>")
def results_by_id(session_id: str):
    summary = session_summary(session_id)
    if not summary["total"]:
        flash("That quiz run has no recorded answers.", "warning")
        return redirect(url_for("main.index"))
    return render_template("results.html", summary=summary)


@bp.route("/progress")
def progress():
    return render_template(
        "progress.html",
        stats=overall_stats(),
        progress=topic_progress(),
        attempts=recent_attempts(limit=30),
    )


@bp.route("/about")
def about():
    return render_template("about.html", modes=MODE_LABELS, quiz_modes=QUIZ_MODES)


# ---------------------------------------------------------------- error pages

@bp.app_errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="Page not found."), 404


@bp.app_errorhandler(413)
def too_large(error):
    return (
        render_template("error.html", code=413, message="That file is too large to import."),
        413,
    )


@bp.app_errorhandler(500)
def server_error(error):  # pragma: no cover - only hit on genuine faults
    db.session.rollback()
    return render_template("error.html", code=500, message="Something went wrong."), 500
