"""WSGI entry point.

    flask run                 # development server (uses .flaskenv)
    gunicorn wsgi:app         # production
    gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app
"""

from __future__ import annotations

from app import create_app
from app.extensions import db
from app.content import sync_topics

app = create_app()


@app.shell_context_processor
def _shell_context() -> dict:
    """Make the models available in ``flask shell``."""
    from app.models import Attempt, Question, Topic

    return {"db": db, "Question": Question, "Topic": Topic, "Attempt": Attempt}


def _prepare_database() -> None:
    """Create tables (and the syllabus topics) if they do not exist yet.

    This keeps a fresh deployment working without a manual migration step. It is
    a no-op once the database exists.
    """
    with app.app_context():
        db.create_all()
        created = sync_topics()
        if created:
            db.session.commit()


_prepare_database()


if __name__ == "__main__":  # pragma: no cover
    # Only used for `python wsgi.py`; prefer `flask run` or gunicorn.
    app.run(debug=app.config.get("DEBUG", False))
