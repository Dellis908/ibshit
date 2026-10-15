"""Flask extension singletons.

Kept separate from the app factory so that models and blueprints can import
``db`` without creating circular imports.
"""

from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
