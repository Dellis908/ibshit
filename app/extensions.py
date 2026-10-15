"""Flask extension singletons.

Kept separate from the app factory so that models and blueprints can import
``db`` without creating circular imports.
"""

from __future__ import annotations

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

#: Session-based authentication. ``login_view`` is where anonymous users are sent
#: when they open a protected page.
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Sign in to keep your progress."
login_manager.login_message_category = "warning"
#: "strong" rotates the session identifier on login, which prevents session
#: fixation (an attacker cannot pre-set a session id and reuse it after login).
login_manager.session_protection = "strong"
