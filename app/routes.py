"""Main web routes: dashboard, topics, quiz, results and progress."""

from __future__ import annotations

import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from flask_login import current_user, login_required

from app.extensions import db
from sqlalchemy import func

from app.models import Question, Topic, overall_stats
from app.notes import LEVELS, load_manifest, notes_by_topic
from app.quiz import (
    MODE_LABELS,
    QUIZ_MODES,
    build_quiz,
    parse_quiz_request,
    recent_attempts,
    session_summary,
    topic_progress,
)
from app.topics import TOPICS_BY_SLUG, topic_groups

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


def grouped_topics() -> tuple[dict, ...]:
    """The whole catalogue as display groups, each with its own heading.

    The 2025 syllabus reads as four themes (A–D) numbered ``1.1`` upwards; the
    2024 material is one block after them. Every catalogue entry is listed, even
    with no questions yet, so the page doubles as a syllabus map and gaps are
    visible instead of silently missing.
    """
    # Count in SQL: len(topic.questions) would load every question row of every
    # topic just to produce a number for the heading.
    counts = dict(
        db.session.query(Question.topic_id, func.count(Question.id))
        .group_by(Question.topic_id)
        .all()
    )
    rows = {topic.slug: topic for topic in db.session.query(Topic).all()}

    groups = []
    for heading, specs in topic_groups():
        entries = []
        for spec in specs:
            topic = rows.get(spec.slug)
            if topic is None:
                continue
            entries.append((topic, counts.get(topic.id, 0)))
        if not entries:
            continue
        groups.append(
            {
                "heading": heading,
                "letter": heading.split(" ")[0] if heading[:1].isalpha() else "",
                "topics": [topic for topic, _count in entries],
                "counts": {topic.slug: count for topic, count in entries},
                "questions": sum(count for _topic, count in entries),
            }
        )
    return tuple(groups)


@bp.route("/")
def index():
    """The dashboard, or a short landing page for visitors."""
    if not current_user.is_authenticated:
        # Nothing here is user-specific, so this needs no login: the bank size is
        # public information and no progress is shown.
        return render_template("landing.html", stats=overall_stats(None))

    stats = overall_stats(current_user)
    progress = {row["slug"]: row for row in topic_progress(current_user)}
    return render_template(
        "index.html",
        stats=stats,
        topics=available_topics(),
        progress=progress,
        modes=MODE_LABELS,
    )


@bp.route("/topics")
@login_required
def topics():
    progress = {row["slug"]: row for row in topic_progress(current_user)}
    return render_template(
        "topics.html",
        groups=grouped_topics(),
        progress=progress,
    )


@bp.route("/topics/<slug>")
@login_required
def topic_detail(slug: str):
    topic = db.session.query(Topic).filter_by(slug=slug).one_or_none()
    if topic is None or not topic.questions:
        flash(f"No questions available for '{slug}' yet.", "warning")
        return redirect(url_for("main.topics"))
    progress = {row["slug"]: row for row in topic_progress(current_user)}
    return render_template(
        "topic_detail.html",
        topic=topic,
        progress=progress.get(slug),
        modes=MODE_LABELS,
        notes=notes_by_topic(current_app.config["DATA_DIR"]).get(slug, []),
    )


@bp.route("/notes/<topic_slug>/<level>")
@login_required
def note_file(topic_slug: str, level: str):
    """Serve one topic's notes.

    Inline so the browser can display the PDF; the download attributes on the
    links in the template let the user pull it down instead. The path is built
    from the manifest rather than from the request, so a crafted URL cannot walk
    outside the notes directory.
    """
    level = level.upper()
    if level not in LEVELS:
        abort(404)
    entry = next(
        (
            note
            for note in load_manifest(current_app.config["DATA_DIR"])
            if note["topic"] == topic_slug and note.get("level") == level
        ),
        None,
    )
    if entry is None:
        abort(404)
    directory = current_app.config["NOTES_DIR"]
    if not (directory / entry["file"]).is_file():
        # The manifest promises a file this deployment never installed. Say so
        # rather than serving a broken viewer.
        abort(404)
    return send_from_directory(
        directory,
        entry["file"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=entry.get("title") or entry["file"],
    )


@bp.route("/quiz")
@login_required
def quiz():
    """Build and present a quiz from query parameters."""
    start_new_session()
    default_count = current_app.config.get("DEFAULT_QUESTION_COUNT", 10)
    max_count = current_app.config.get("MAX_QUESTION_COUNT", 60)

    req = parse_quiz_request(request.args, defaults={"count": default_count})
    req.count = max(1, min(req.count, max_count))

    questions = build_quiz(req, user=current_user, max_count=max_count)
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
@login_required
def quiz_by_topic(topic: str):
    """Backwards-compatible shortcut for ``/quiz?topic=<topic>``."""
    args = request.args.to_dict(flat=False)
    args.setdefault("topic", [topic])
    args.setdefault("mode", ["practice"])
    query = "&".join(f"{key}={value}" for key, values in args.items() for value in values)
    return redirect(f"{url_for('main.quiz')}?{query}")


@bp.route("/results")
@login_required
def results():
    sid = session.get("quiz_session")
    if not sid:
        flash("Finish a quiz to see your results.", "warning")
        return redirect(url_for("main.index"))
    summary = session_summary(sid, current_user)
    if not summary["total"]:
        flash("No answers recorded for the last quiz.", "warning")
        return redirect(url_for("main.index"))
    return render_template("results.html", summary=summary)


@bp.route("/results/<session_id>")
@login_required
def results_by_id(session_id: str):
    summary = session_summary(session_id, current_user)
    if not summary["total"]:
        flash("That quiz run has no recorded answers.", "warning")
        return redirect(url_for("main.index"))
    return render_template("results.html", summary=summary)


@bp.route("/progress")
@login_required
def progress():
    return render_template(
        "progress.html",
        stats=overall_stats(current_user),
        progress=topic_progress(current_user),
        attempts=recent_attempts(current_user, limit=30),
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
