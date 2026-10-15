"""Application factory for the IB Biology quiz.

Usage::

    from app import create_app
    app = create_app()              # development config
    app = create_app("testing")     # in-memory SQLite, for tests

The legacy ``from app import db`` import keeps working: ``db`` is re-exported
here from :mod:`app.extensions`.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import Flask, jsonify, render_template

from app.config import apply_environment, get_config
from app.extensions import db, login_manager

__all__ = ["create_app", "db"]


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))

    # Resolve environment-dependent settings now, so the same process can create
    # differently configured apps (and tests can change the environment).
    apply_environment(app, testing=app.config.get("TESTING", False))

    # The instance folder holds the local SQLite database.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _check_configuration(app)
    _init_extensions(app)
    _register_security(app)
    _init_proxy_support(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()
        # create_all does not alter existing tables, so columns added after the
        # first release are applied here. Failures are logged, not fatal.
        from app.db_migrate import report_orphans, run_migrations

        applied = run_migrations()
        if applied:
            app.logger.info("schema updated: %s", ", ".join(applied))
        report_orphans()

    return app


def _check_configuration(app: Flask) -> None:
    """Fail fast, and legibly, on a misconfigured deployment.

    A missing SECRET_KEY otherwise surfaces as a stack trace on the first
    request that touches the session, which is a confusing way to learn that an
    environment file is wrong. Production deliberately rejects the development
    placeholder.
    """
    if app.config.get("TESTING"):
        return

    secret = app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY is not set. Generate one and make it available to the "
            "app, e.g.\n"
            "  echo \"SECRET_KEY=$(openssl rand -hex 32)\" >> /etc/ibshit.env\n"
            "then restart the service. See DEPLOY.md step 4."
        )

    if not app.config.get("DEBUG") and secret == "dev-secret-change-me":
        raise RuntimeError(
            "SECRET_KEY is still the development placeholder. Set a real value "
            "before running in production. See DEPLOY.md step 4."
        )


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    login_manager.init_app(app)


def _register_security(app: Flask) -> None:
    """Apply CSRF protection to every state-changing request by default."""
    from app.security import csrf_protect

    app.before_request(csrf_protect)


def _init_proxy_support(app: Flask) -> None:
    """Trust ``X-Forwarded-*`` headers when explicitly configured.

    This is off by default: if the app is reachable directly, trusting a client
    supplied ``X-Forwarded-Proto`` would let anyone claim their request is HTTPS.
    Turn it on with ``TRUST_PROXY_HEADERS=1`` only when a reverse proxy you
    control sets those headers.
    """
    if not app.config.get("TRUST_PROXY_HEADERS"):
        return
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _register_blueprints(app: Flask) -> None:
    from app.api import api
    from app.auth import bp as auth_bp
    from app.routes import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api)

    @app.route("/healthz")
    def healthz():
        """Lightweight liveness probe for deployments."""
        return jsonify({"status": "ok"})


def _register_template_helpers(app: Flask) -> None:
    """Values every template needs (nav, CSRF token, active page)."""
    from flask_login import current_user

    from app.api import csrf_token
    from app.models import overall_stats

    @app.context_processor
    def inject_globals():
        # Progress figures are per account, so a visitor sees only the bank size.
        try:
            stats = overall_stats(current_user if current_user.is_authenticated else None)
        except Exception:  # pragma: no cover - tables may not exist yet
            stats = {"total_questions": 0, "total_attempts": 0, "accuracy": None}
        from app.auth import registration_allowed

        return {
            "csrf_token": csrf_token(),
            "site_stats": stats,
            "site_name": "IB Biology Revision",
            "registration_open": registration_allowed(),
            "invite_required": bool(app.config.get("REGISTRATION_CODE")),
        }

    @app.template_filter("nl2br")
    def nl2br(value: str) -> str:
        """Render newlines in user-supplied text as <br> tags (escaped first)."""
        from markupsafe import Markup, escape

        if not value:
            return ""
        return Markup("<br>".join(escape(value).split("\n")))

    @app.template_filter("mirror_images")
    def mirror_images(value: str) -> str:
        """Route remote <img> tags through the local mirror endpoint.

        Question content is rendered with ``|safe``, so image sources are
        rewritten here rather than in the browser, and only on allow-listed
        hosts. Anything else is left untouched for the sanitiser to reject.
        """
        from urllib.parse import quote

        from markupsafe import Markup

        from app.sanitize import _image_src_allowed

        if not value:
            return ""

        def replace(match):
            url = match.group(1)
            if not _image_src_allowed(url):
                return match.group(0)
            return f'<img src="/api/image?url={quote(url, safe="")}"'

        # Match only the opening of the tag so other attributes are preserved.
        rewritten = re.sub(r'<img\s+src="([^"]+)"', replace, str(value), flags=re.IGNORECASE)
        return Markup(rewritten)

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
