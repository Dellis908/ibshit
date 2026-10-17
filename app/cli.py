"""``flask quiz`` command group.

    flask quiz init                  create tables and topics
    flask quiz seed                  load seed/questions.json
    flask quiz import FILE.jsonl     import a custom file
    flask quiz export out.json       export the bank
    flask quiz check                 validate the seed file without writing
    flask quiz stats                 print progress statistics
    flask quiz reset --yes           delete recorded attempts
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from flask import Flask, current_app

from app.content import (
    export_questions,
    import_file,
    load_payload,
    sync_topics,
    validate_question,
)
from app.extensions import db
from app.models import Attempt, Question, Topic, overall_stats
from app.quiz import topic_progress


def register_cli(app: Flask) -> None:
    """Attach the ``quiz`` and ``auth`` command groups to ``app``."""

    @app.cli.group("auth")
    def auth_group():
        """Manage user accounts."""

    @auth_group.command("list")
    def auth_list():
        """List accounts and how much each has answered."""
        from app.models import User

        users = db.session.query(User).order_by(User.id).all()
        if not users:
            click.echo("No accounts yet. Create one in the browser, or use:")
            click.echo("  flask auth create-admin you@example.com")
            return
        for user in users:
            answers = db.session.query(Attempt).filter(Attempt.user_id == user.id).count()
            flag = "admin" if user.is_admin else "     "
            last = user.last_login_at.strftime("%Y-%m-%d %H:%M") if user.last_login_at else "never"
            click.echo(f"  {user.id:>4}  {flag}  {user.email:<38} {answers:>6} answers  last login {last}")

    @auth_group.command("create")
    @click.argument("email")
    @click.option("--name", default=None, help="Display name.")
    @click.option("--admin", is_flag=True, help="Grant administrator rights.")
    @click.password_option(confirmation_prompt=True)
    def auth_create(email: str, name: str | None, admin: bool, password: str):
        """Create an account non-interactively."""
        from app.auth import normalise_email, validate_signup
        from app.models import User, user_count

        email = normalise_email(email)
        problems = validate_signup(email, password, password, name or "")
        if problems:
            for problem in problems:
                click.secho(f"  - {problem}", fg="red")
            raise SystemExit(1)
        if db.session.query(User).filter_by(email=email).one_or_none():
            click.secho(f"An account with {email} already exists.", fg="red")
            raise SystemExit(1)

        user = User(email=email, display_name=name, is_admin=admin or user_count() == 0)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.secho(f"Created {'administrator' if user.is_admin else 'user'} {email}", fg="green")

    @auth_group.command("create-admin")
    @click.argument("email")
    @click.option("--name", default=None, help="Display name.")
    @click.password_option(confirmation_prompt=True)
    def auth_create_admin(email: str, name: str | None, password: str):
        """Create an administrator account (convenience wrapper)."""
        from app.auth import normalise_email, validate_signup
        from app.models import User

        email = normalise_email(email)
        problems = validate_signup(email, password, password, name or "")
        if problems:
            for problem in problems:
                click.secho(f"  - {problem}", fg="red")
            raise SystemExit(1)
        if db.session.query(User).filter_by(email=email).one_or_none():
            click.secho(f"An account with {email} already exists.", fg="red")
            raise SystemExit(1)

        user = User(email=email, display_name=name, is_admin=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.secho(f"Created administrator {email}", fg="green")

    @auth_group.command("passwd")
    @click.argument("email")
    @click.password_option(confirmation_prompt=True)
    def auth_passwd(email: str, password: str):
        """Reset an account password (for when someone is locked out)."""
        from app.auth import normalise_email, validate_signup
        from app.models import User

        user = db.session.query(User).filter_by(email=normalise_email(email)).one_or_none()
        if user is None:
            click.secho(f"No account with email {email!r}", fg="red")
            raise SystemExit(1)
        problems = validate_signup(user.email, password, password, "")
        if problems:
            for problem in problems:
                click.secho(f"  - {problem}", fg="red")
            raise SystemExit(1)
        user.set_password(password)
        db.session.commit()
        click.secho(f"Password updated for {user.email}", fg="green")

    @auth_group.command("promote")
    @click.argument("email")
    def auth_promote(email: str):
        """Grant administrator rights to an existing account."""
        from app.auth import normalise_email
        from app.models import User

        user = db.session.query(User).filter_by(email=normalise_email(email)).one_or_none()
        if user is None:
            click.secho(f"No account with email {email!r}", fg="red")
            raise SystemExit(1)
        user.is_admin = True
        db.session.commit()
        click.secho(f"{user.email} is now an administrator", fg="green")

    @auth_group.command("adopt-attempts")
    @click.argument("email")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def auth_adopt(email: str, yes: bool):
        """Give answer history recorded before accounts existed to one account."""
        from app.auth import adopt_orphan_attempts

        orphans = db.session.query(Attempt).filter(Attempt.user_id.is_(None)).count()
        if not orphans:
            click.echo("There are no ownerless answers to adopt.")
            return
        if not yes:
            click.confirm(f"Assign {orphans} ownerless answer(s) to {email}?", abort=True)
        try:
            updated = adopt_orphan_attempts(email)
        except ValueError as exc:
            click.secho(str(exc), fg="red")
            raise SystemExit(1)
        click.secho(f"Assigned {updated} answer(s).", fg="green")

    @app.cli.group("quiz")
    def quiz_group():
        """Manage the question bank."""

    # ------------------------------------------------------------------- init
    @quiz_group.command("init")
    def init_command():
        """Create the database tables and the syllabus topics."""
        db.create_all()
        created = sync_topics()
        db.session.commit()
        click.secho(f"Tables ready. Topics created: {created}", fg="green")

    # ------------------------------------------------------------------- seed
    @quiz_group.command("seed")
    def seed_command():
        """Load the bundled seed question bank."""
        path = Path(current_app.config["SEED_FILE"])
        db.create_all()
        sync_topics()
        db.session.commit()

        report = import_file(path, default_topic=None)
        click.secho(report.summary(), fg="green")
        _print_bank_size()

    # -------------------------------------------------------------- ai grading
    @quiz_group.command("ai-check")
    def ai_check_command():
        """Report whether second-opinion grading is configured and reachable."""
        from app import ai_grading

        config = current_app.config
        key = config.get("OPENROUTER_API_KEY")
        click.echo(f"enabled:  {bool(config.get('AI_GRADING_ENABLED'))}")
        click.echo(f"model:    {config.get('OPENROUTER_MODEL') or ai_grading.DEFAULT_MODEL}")
        click.echo(f"key:      {'set (' + key[:6] + '...)' if key else 'missing'}")
        if not ai_grading.enabled(config):
            click.secho("grading will not call out: set OPENROUTER_API_KEY (and AI_GRADING_ENABLED=1)", fg="yellow")
            return
        click.echo("contacting OpenRouter…")
        try:
            import urllib.request

            request = urllib.request.Request(
                ai_grading.API_URL,
                data=json.dumps(
                    {
                        "model": config.get("OPENROUTER_MODEL") or ai_grading.DEFAULT_MODEL,
                        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                        "max_tokens": 5,
                    }
                ).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=config.get("AI_GRADING_TIMEOUT", 6)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            reply = payload["choices"][0]["message"]["content"].strip()
            click.secho(f"OK — model replied: {reply[:60]!r}", fg="green")
        except Exception as exc:  # pragma: no cover - needs the network
            click.secho(f"FAILED: {exc}", fg="red")

    # ----------------------------------------------------------------- import
    @quiz_group.command("import")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--topic", default=None, help="Default topic for questions without one.")
    @click.option("--dry-run", is_flag=True, help="Validate only; write nothing.")
    def import_command(path: Path, topic: str | None, dry_run: bool):
        """Import questions from a JSON or JSON Lines file."""
        db.create_all()
        sync_topics()
        db.session.commit()

        report = import_file(path, default_topic=topic, dry_run=dry_run)
        click.secho(("DRY RUN — " if dry_run else "") + report.summary(), fg="green")
        if not dry_run:
            _print_bank_size()

    # ----------------------------------------------------------------- export
    @quiz_group.command("export")
    @click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--topic", default=None, help="Export a single topic.")
    @click.option("--no-answers", is_flag=True, help="Omit answers (for worksheets).")
    @click.option("--stats", is_flag=True, help="Include per-question attempt stats.")
    def export_command(path: Path, topic: str | None, no_answers: bool, stats: bool):
        """Export the question bank to a JSON file."""
        payload = export_questions(
            topic_slug=topic, include_answers=not no_answers, include_stats=stats
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        click.secho(f"Exported {payload['meta']['count']} questions to {path}", fg="green")

    # ------------------------------------------------------------------ check
    @quiz_group.command("check")
    @click.argument(
        "path",
        required=False,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    def check_command(path: Path | None):
        """Validate a question file (defaults to the bundled seed file)."""
        target = path or Path(current_app.config["SEED_FILE"])
        questions, meta = load_payload(target)
        problems: list[str] = []
        for index, raw in enumerate(questions):
            problems.extend(validate_question(raw, index))

        click.echo(f"file:        {target}")
        click.echo(f"questions:   {len(questions)}")
        if meta:
            click.echo(f"meta:        {meta}")
        if problems:
            click.secho(f"problems:    {len(problems)}", fg="red")
            for problem in problems[:40]:
                click.echo(f"  - {problem}")
            raise SystemExit(1)
        click.secho("problems:    0 — file is valid", fg="green")

    # ------------------------------------------------------------------ stats
    @quiz_group.command("stats")
    def stats_command():
        """Print question-bank and progress statistics."""
        stats = overall_stats()
        click.echo(f"questions in bank: {stats['total_questions']}")
        click.echo(f"attempts recorded: {stats['total_attempts']}")
        click.echo(f"questions seen:    {stats['questions_seen']} ({stats['coverage']}%)")
        accuracy = stats["accuracy"]
        click.echo(f"accuracy:          {'—' if accuracy is None else f'{accuracy}%'}")
        click.echo("")
        for row in topic_progress():
            accuracy = "—" if row["accuracy"] is None else f"{row['accuracy']}%"
            click.echo(
                f"  {row['title'][:42]:<44} "
                f"{row['question_count']:>4} questions  "
                f"{row['attempted']:>4} seen  "
                f"{accuracy:>7}"
            )

    # ------------------------------------------------------------------ reset
    @quiz_group.command("reset")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    @click.option("--questions", is_flag=True, help="Also delete the question bank.")
    def reset_command(yes: bool, questions: bool):
        """Delete recorded attempts (and optionally the question bank)."""
        if not yes:
            click.confirm("Delete all recorded attempts?", abort=True)
        deleted = db.session.query(Attempt).delete(synchronize_session=False)
        message = f"Deleted {deleted} attempts."
        if questions:
            deleted_questions = db.session.query(Question).delete(synchronize_session=False)
            db.session.query(Topic).delete(synchronize_session=False)
            message += f" Deleted {deleted_questions} questions and all topics."
        db.session.commit()
        click.secho(message, fg="green")


def _print_bank_size() -> None:
    total = db.session.query(db.func.count(Question.id)).scalar() or 0
    topics = db.session.query(db.func.count(Topic.id)).scalar() or 0
    click.echo(f"Bank now holds {total} questions across {topics} topics.")
