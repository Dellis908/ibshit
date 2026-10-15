"""Application configuration.

Everything is driven by environment variables with sane local defaults, so the
app runs out of the box from a fresh clone (``flask run``) and can still be
deployed with a real database by setting ``DATABASE_URL``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# <repo>/app/config.py -> <repo>
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _database_uri() -> str:
    """Resolve the SQLAlchemy database URI.

    ``DATABASE_URL`` wins if set. Otherwise we use a SQLite file inside the
    repository's ``instance/`` directory instead of a hardcoded absolute path,
    so the app works wherever it is checked out.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Heroku/Render style postgres:// URLs are rejected by SQLAlchemy 1.4+
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    instance_dir = BASE_DIR / "instance"
    instance_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{instance_dir / 'quiz.db'}"


class Config:
    """Base configuration shared by every environment.

    Environment-dependent values are resolved by :func:`apply_environment` when
    the app is created, not when this module is imported. Reading them at import
    time would freeze configuration for the whole process, which makes the
    settings untestable and ignores changes made later.

    The attributes below are the defaults; the environment overrides them.
    """

    BASE_DIR = BASE_DIR

    #: Overridden by SECRET_KEY; production refuses to start without it.
    SECRET_KEY = "dev-secret-change-me"

    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / 'instance' / 'quiz.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    DATA_DIR = BASE_DIR / "data"
    SEED_FILE = BASE_DIR / "seed" / "questions.json"

    FUZZY_THRESHOLD = 72.0
    NUMERIC_TOLERANCE = 0.01
    DEFAULT_QUESTION_COUNT = 10
    MAX_QUESTION_COUNT = 60
    QUIZ_SECONDS_PER_MARK = 60

    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    JSON_SORT_KEYS = False

    # Session cookie hardening; SECURE is opt-in for HTTPS deployments.
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "http"
    TRUST_PROXY_HEADERS = False

    #: When set, /api/import and /api/export require an X-API-Key header.
    API_KEY = None


class DevelopmentConfig(Config):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionConfig(Config):
    DEBUG = False
    # Deliberately empty: _check_configuration turns this into a clear error
    # rather than letting the app run with a guessable session key.
    SECRET_KEY = ""


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None):
    """Return the config class for ``name`` (falls back to ``FLASK_ENV``)."""
    if name is None:
        name = os.getenv("FLASK_ENV", "default")
    return CONFIGS.get(name, DevelopmentConfig)


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _number(name: str, default):
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def apply_environment(app, *, testing: bool = False) -> None:
    """Overlay environment variables onto an app's configuration.

    Called by the app factory so that settings are read at creation time. In
    testing mode a fixed secret is supplied, because session-based behaviour is
    exercised constantly and a real key would be noise.
    """
    if testing:
        app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or "testing-secret"
        return

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", app.config.get("SECRET_KEY", ""))

    if "DATABASE_URL" in os.environ:
        app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri()

    app.config["FUZZY_THRESHOLD"] = _number("QUIZ_FUZZY_THRESHOLD", app.config["FUZZY_THRESHOLD"])
    app.config["NUMERIC_TOLERANCE"] = _number("QUIZ_NUMERIC_TOLERANCE", app.config["NUMERIC_TOLERANCE"])
    app.config["DEFAULT_QUESTION_COUNT"] = _number(
        "QUIZ_DEFAULT_COUNT", app.config["DEFAULT_QUESTION_COUNT"]
    )
    app.config["MAX_QUESTION_COUNT"] = _number("QUIZ_MAX_COUNT", app.config["MAX_QUESTION_COUNT"])
    app.config["QUIZ_SECONDS_PER_MARK"] = _number(
        "QUIZ_SECONDS_PER_MARK", app.config["QUIZ_SECONDS_PER_MARK"]
    )
    app.config["MAX_CONTENT_LENGTH"] = _number(
        "QUIZ_MAX_UPLOAD_BYTES", app.config["MAX_CONTENT_LENGTH"]
    )

    app.config["SESSION_COOKIE_SECURE"] = _flag("SESSION_COOKIE_SECURE")
    app.config["TRUST_PROXY_HEADERS"] = _flag("TRUST_PROXY_HEADERS")
    app.config["PREFERRED_URL_SCHEME"] = os.getenv(
        "PREFERRED_URL_SCHEME", app.config.get("PREFERRED_URL_SCHEME", "http")
    )

    app.config["API_KEY"] = os.getenv("API_KEY") or None

    if os.getenv("QUIZ_DATA_DIR"):
        app.config["DATA_DIR"] = Path(os.environ["QUIZ_DATA_DIR"])
    if os.getenv("QUIZ_SEED_FILE"):
        app.config["SEED_FILE"] = Path(os.environ["QUIZ_SEED_FILE"])
