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
    """Base configuration shared by every environment."""

    BASE_DIR = BASE_DIR

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Paths
    DATA_DIR = Path(os.getenv("QUIZ_DATA_DIR", BASE_DIR / "data"))
    SEED_FILE = Path(os.getenv("QUIZ_SEED_FILE", BASE_DIR / "seed" / "questions.json"))

    # Grading / quiz behaviour
    FUZZY_THRESHOLD = float(os.getenv("QUIZ_FUZZY_THRESHOLD", 72))
    NUMERIC_TOLERANCE = float(os.getenv("QUIZ_NUMERIC_TOLERANCE", 0.01))
    DEFAULT_QUESTION_COUNT = int(os.getenv("QUIZ_DEFAULT_COUNT", 10))
    MAX_QUESTION_COUNT = int(os.getenv("QUIZ_MAX_COUNT", 60))

    # Uploads / imports
    MAX_CONTENT_LENGTH = int(os.getenv("QUIZ_MAX_UPLOAD_BYTES", 8 * 1024 * 1024))
    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionConfig(Config):
    DEBUG = False
    # Fail loudly in production if the operator forgot to set a real secret.
    SECRET_KEY = os.getenv("SECRET_KEY") or ""


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
