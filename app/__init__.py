"""Application factory for the IB Biology quiz.

Usage::

    from app import create_app
    app = create_app()              # development config
    app = create_app("testing")     # in-memory SQLite, for tests

The legacy ``from app import db`` import keeps working: ``db`` is re-exported
here from :mod:`app.extensions`.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template

from app.config import get_config
from app.extensions import db

__all__ = ["create_app", "db"]


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))

    # The instance folder holds the local SQLite database.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _init_extensions(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()

    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from app.api import api
    from app.routes import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api)

    @app.route("/healthz")
    def healthz():
        """Lightweight liveness probe for deployments."""
        return jsonify({"status": "ok"})


def _register_template_helpers(app: Flask) -> None:
    """Values every template needs (nav, CSRF token, active page)."""
    from app.api import csrf_token
    from app.models import overall_stats

    @app.context_processor
    def inject_globals():
        try:
            stats = overall_stats()
        except Exception:  # pragma: no cover - tables may not exist yet
            stats = {"total_questions": 0, "total_attempts": 0, "accuracy": None}
        return {
            "csrf_token": csrf_token(),
            "site_stats": stats,
            "site_name": "IB Biology Revision",
        }

    @app.template_filter("nl2br")
    def nl2br(value: str) -> str:
        """Render newlines in user-supplied text as <br> tags (escaped first)."""
        from markupsafe import Markup, escape

        if not value:
            return ""
        return Markup("<br>".join(escape(value).split("\n")))

    @app.template_filter("percent")
    def percent(value, default: str = "—") -> str:
        if value is None:
            return default
        return f"{value:.1f}%"

    @app.errorhandler(404)
    def handle_404(error):
        from flask import request

        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found", "path": request.path}), 404
        return render_template("error.html", code=404, message="Page not found."), 404


def _register_cli(app: Flask) -> None:
    """Attach the ``flask quiz ...`` command group."""
    from app.cli import register_cli

    register_cli(app)
