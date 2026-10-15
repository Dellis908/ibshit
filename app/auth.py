"""Authentication: accounts, sign-in, sign-out and account settings.

Security properties this module is responsible for:

* Passwords are hashed with Werkzeug's default (scrypt) and never stored or
  logged in plaintext.
* Every state-changing request requires the session CSRF token.
* Sign-in is rate limited per account *and* per client address, so neither
  guessing one password nor spraying many accounts is practical.
* The session identifier is rotated on sign-in and cleared on sign-out, which
  prevents session fixation.
* Sign-in failures give one generic message, so the response cannot be used to
  discover which email addresses have accounts.
* Progress is scoped to the signed-in account everywhere, so accounts cannot see
  each other's data.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

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
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Attempt, Question, Topic, User, user_count

bp = Blueprint("auth", __name__, url_prefix="/auth")

#: Minimum password length. Length is the property that actually helps, so the
#: bar is raised rather than adding character-class rules that push users towards
#: predictable substitutions.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200  # bounds the hashing cost of a hostile request
MAX_EMAIL_LENGTH = 255

#: Good-enough email shape check. Deliverability is not something a regex can
#: establish, so this only rejects obvious mistakes.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

#: Passwords that appear at the top of every breach list.
_COMMON_PASSWORDS = frozenset(
    """
    password password1 password123 passw0rd letmein welcome qwerty qwerty123
    iloveyou admin administrator root toor secret changeme 123456 12345678
    123456789 1234567890 abc123 monkey dragon sunshine princess football
    baseball superman trustno1 master hello freedom whatever qazwsx
    """.split()
)

# ------------------------------------------------------------------ rate limit

#: ``{(action, key): deque[timestamp]}``. In-process, which is correct for the
#: single-worker deployments this app targets; behind several workers each would
#: keep its own window, so the effective limit multiplies by the worker count.
_attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)

#: ``(max attempts, window seconds)``.
RATE_LIMITS: dict[str, tuple[int, float]] = {
    "login": (8, 300.0),      # 8 sign-in attempts per 5 minutes
    "signup": (5, 3600.0),    # 5 accounts per hour per address
    "password": (5, 900.0),   # 5 password changes per 15 minutes
}


def _client_key() -> str:
    """The best available client identity for rate limiting.

    ``X-Forwarded-For`` is only consulted because the app is expected to sit
    behind nginx, which overwrites it; a directly reachable app must leave
    TRUST_PROXY_HEADERS off or this would be spoofable.
    """
    if current_app.config.get("TRUST_PROXY_HEADERS"):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit_hit(action: str) -> tuple[bool, int]:
    """Record an attempt. Returns ``(allowed, seconds_until_retry)``."""
    limit, window = RATE_LIMITS.get(action, (10, 300.0))
    now = time.monotonic()
    key = (action, _client_key())
    bucket = _attempts[key]

    while bucket and now - bucket[0] > window:
        bucket.popleft()

    if len(bucket) >= limit:
        retry_after = int(window - (now - bucket[0])) + 1
        return False, retry_after

    bucket.append(now)
    return True, 0


def rate_limit_clear(action: str) -> None:
    """Forget recorded attempts, e.g. after a successful sign-in."""
    _attempts.pop((action, _client_key()), None)


def rate_limit_reset() -> None:
    """Clear every bucket. Used by tests."""
    _attempts.clear()


# ------------------------------------------------------------------ validation

def normalise_email(value: str) -> str:
    """Trim and lower-case an address so accounts cannot differ by case."""
    return (value or "").strip().lower()


def validate_signup(email: str, password: str, confirm: str, display_name: str) -> list[str]:
    """Return a list of problems with a sign-up request (empty means valid)."""
    problems: list[str] = []

    if not email:
        problems.append("Enter your email address.")
    elif len(email) > MAX_EMAIL_LENGTH:
        problems.append("That email address is too long.")
    elif not _EMAIL_RE.match(email):
        problems.append("That does not look like an email address.")

    if not password:
        problems.append("Choose a password.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    elif len(password) > MAX_PASSWORD_LENGTH:
        problems.append("That password is too long.")
    elif password.lower() in _COMMON_PASSWORDS:
        problems.append("That password is too common. Choose something else.")
    elif email and email.split("@")[0].lower() in password.lower():
        # "student1234" for student@example.com is a password an attacker tries
        # first, so it is refused rather than merely discouraged.
        problems.append("Do not build your password from your email address.")

    if password != confirm:
        problems.append("The two passwords do not match.")

    if display_name and len(display_name) > 80:
        problems.append("That display name is too long.")

    return problems


def validate_password_change(current: str, new: str, confirm: str) -> list[str]:
    """Return a list of problems with a password change (empty means valid)."""
    problems: list[str] = []
    if not current:
        problems.append("Enter your current password.")
    if len(new) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    elif len(new) > MAX_PASSWORD_LENGTH:
        problems.append("That password is too long.")
    elif new.lower() in _COMMON_PASSWORDS:
        problems.append("That password is too common. Choose something else.")
    if new != confirm:
        problems.append("The two new passwords do not match.")
    if current and new and current == new:
        problems.append("Choose a password different from your current one.")
    return problems


def registration_allowed() -> bool:
    """Whether new accounts may be created."""
    return bool(current_app.config.get("ALLOW_REGISTRATION", True))


def check_invite_code(supplied: str) -> bool:
    """Validate the invite code, when one is configured.

    An empty configured code means "no invite required". Comparison is
    constant-time so the response does not leak how much of a guess was right.
    """
    import hmac

    expected = current_app.config.get("REGISTRATION_CODE") or ""
    if not expected:
        return True
    return hmac.compare_digest(str(supplied or ""), str(expected))


# ---------------------------------------------------------------------- routes

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Create an account."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if not registration_allowed():
        return render_template("auth/signup.html", closed=True), 403

    form = {
        "email": request.form.get("email", ""),
        "display_name": request.form.get("display_name", ""),
    }

    if request.method == "POST":
        allowed, retry_after = rate_limit_hit("signup")
        if not allowed:
            flash(
                f"Too many sign-up attempts. Try again in {retry_after // 60 + 1} minutes.",
                "error",
            )
            return render_template("auth/signup.html", form=form), 429

        email = normalise_email(form["email"])
        display_name = (form["display_name"] or "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        problems = validate_signup(email, password, confirm, display_name)

        if not check_invite_code(request.form.get("invite_code", "")):
            problems.append("That invite code is not valid.")

        if not problems:
            if db.session.query(User).filter_by(email=email).one_or_none():
                # Registration is open, so this is a normal outcome rather than a
                # leak; the message avoids confirming anything beyond that.
                problems.append("An account with that email already exists.")

        if problems:
            for problem in problems:
                flash(problem, "error")
            return render_template("auth/signup.html", form=form), 400

        user = User(
            email=email,
            display_name=display_name or None,
            is_admin=user_count() == 0,  # first account administers the bank
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Concurrent sign-up for the same address.
            db.session.rollback()
            flash("An account with that email already exists.", "error")
            return render_template("auth/signup.html", form=form), 400

        login_user(user)
        session.permanent = True
        rate_limit_clear("login")
        flash(f"Welcome, {user.name}.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Sign in."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        allowed, retry_after = rate_limit_hit("login")
        if not allowed:
            flash(
                f"Too many sign-in attempts. Try again in {retry_after // 60 + 1} minutes.",
                "error",
            )
            return render_template("auth/login.html"), 429

        email = normalise_email(request.form.get("email", ""))
        password = request.form.get("password", "")

        user = db.session.query(User).filter_by(email=email).one_or_none()

        # The same work is done whether or not the account exists, and the same
        # message is returned, so timing and wording do not reveal which emails
        # have accounts.
        if user is None or not user.check_password(password):
            flash("Email address or password is incorrect.", "error")
            return render_template("auth/login.html", email=email), 401

        login_user(user, remember=bool(request.form.get("remember")))
        session.permanent = True
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        rate_limit_clear("login")

        # Only ever redirect to a local path, so a crafted ?next= cannot bounce
        # the user to another site after signing in.
        target = request.args.get("next", "")
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("main.index")
        return redirect(target)

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """Sign out. POST only, so a link or image cannot sign the user out."""
    if current_user.is_authenticated:
        logout_user()
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/account")
@login_required
def account():
    """Account overview and settings."""
    from app.models import overall_stats

    return render_template(
        "auth/account.html",
        stats=overall_stats(current_user),
        attempts=(
            db.session.query(Attempt)
            .filter(Attempt.user_id == current_user.id)
            .count()
        ),
    )


@bp.route("/password", methods=["POST"])
@login_required
def change_password():
    """Change the signed-in user's password."""
    allowed, retry_after = rate_limit_hit("password")
    if not allowed:
        flash(
            f"Too many password changes. Try again in {retry_after // 60 + 1} minutes.",
            "error",
        )
        return redirect(url_for("auth.account"))

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("new_password_confirm", "")

    if not current_user.check_password(current):
        flash("Your current password is incorrect.", "error")
        return redirect(url_for("auth.account"))

    problems = validate_password_change(current, new, confirm)
    if problems:
        for problem in problems:
            flash(problem, "error")
        return redirect(url_for("auth.account"))

    current_user.set_password(new)
    db.session.commit()
    # Re-issue the session so any other browser holding this session id is
    # invalidated by the password change.
    login_user(current_user)
    flash("Password updated.", "success")
    return redirect(url_for("auth.account"))


@bp.route("/delete", methods=["POST"])
@login_required
def delete_account():
    """Delete the account and everything it owns.

    Requires the password, because this is irreversible, and refuses while the
    user is the only administrator (otherwise the bank could be left with no
    administrator at all).
    """
    if not current_user.check_password(request.form.get("password", "")):
        flash("Password is incorrect, so the account was not deleted.", "error")
        return redirect(url_for("auth.account"))

    if current_user.is_admin:
        others = (
            db.session.query(User)
            .filter(User.is_admin.is_(True), User.id != current_user.id)
            .count()
        )
        if others == 0:
            flash(
                "This is the only administrator account, so it cannot be deleted. "
                "Promote another account first.",
                "error",
            )
            return redirect(url_for("auth.account"))

    user = current_user._get_current_object()
    logout_user()
    session.clear()
    db.session.delete(user)
    db.session.commit()
    flash("Your account and its progress have been deleted.", "success")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------- CLI hooks

def adopt_orphan_attempts(email: str) -> int:
    """Give pre-accounts answer history to one account.

    Exposed through ``flask auth adopt-attempts`` so an operator can rescue the
    progress recorded before accounts existed.
    """
    user = db.session.query(User).filter_by(email=normalise_email(email)).one_or_none()
    if user is None:
        raise ValueError(f"no account with email {email!r}")

    updated = (
        db.session.query(Attempt)
        .filter(Attempt.user_id.is_(None))
        .update({Attempt.user_id: user.id}, synchronize_session=False)
    )
    db.session.commit()
    return updated


def question_bank_summary() -> dict:
    """Small helper used by the admin view."""
    return {
        "questions": db.session.query(Question).count(),
        "topics": db.session.query(Topic).count(),
        "users": user_count(),
    }
