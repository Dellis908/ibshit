"""Shared request guards.

Kept separate from the blueprints so both the HTML routes and the JSON API can
use the same logic without importing each other.
"""

from __future__ import annotations

import hmac
from functools import wraps

from flask import abort, jsonify, redirect, request, session, url_for
from flask_login import current_user

#: Methods that must carry a valid CSRF token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Endpoints that establish or end a session. They cannot require an existing
#: session (there is none yet), so they are exempt — but each validates the token
#: itself, because a missing session makes ``session["csrf_token"]`` unavailable.
_SELF_PROTECTED_ENDPOINTS = frozenset()


def wants_json() -> bool:
    """Whether the caller expects JSON rather than a redirect."""
    if request.path.startswith("/api/"):
        return True
    return request.accept_mimetypes.best == "application/json"


def _token_from_request() -> str:
    """The CSRF token supplied with a request, from either a form or a header."""
    return (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRF-Token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
        or ""
    )


def csrf_token() -> str:
    """This session's CSRF token, creating it on first use.

    Kept here rather than in the API module so the HTML forms and the JSON API
    share one token and one comparison.
    """
    import secrets

    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_valid() -> bool:
    """Whether the request carries the session's CSRF token."""
    expected = session.get("csrf_token")
    if not expected:
        return False
    supplied = _token_from_request()
    if not supplied:
        return False
    return hmac.compare_digest(str(supplied), str(expected))


def csrf_protect():
    """Reject state-changing requests without a valid CSRF token.

    Registered globally so that **every** POST route is covered by default,
    including any added later. Without this, an HTML form is a CSRF target: the
    browser attaches the session cookie to a cross-site submission automatically,
    which would let another site's page change a password or delete an account.
    """
    if request.method not in UNSAFE_METHODS:
        return None

    # The API performs the same check inside its own guard, so it is skipped here
    # to keep one implementation per request and avoid a double 403.
    if request.path.startswith("/api/"):
        return None

    if csrf_valid():
        return None

    abort(403, description="Invalid or missing CSRF token. Reload the page and try again.")


def login_required_api(view):
    """Like ``login_required``, but answers 401 JSON for API callers.

    A fetch() from the quiz page cannot follow an HTML redirect to a sign-in
    form usefully, so API callers get a status they can act on.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            if wants_json():
                return jsonify({"error": "authentication_required"}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    """Restrict a view to administrator accounts."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            if wants_json():
                return jsonify({"error": "authentication_required"}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        if not getattr(current_user, "is_admin", False):
            if wants_json():
                return jsonify({"error": "administrator_required"}), 403
            return jsonify({"error": "administrator_required"}), 403
        return view(*args, **kwargs)

    return wrapper
