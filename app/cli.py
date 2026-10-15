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
    """Attach the ``quiz`` command group to ``app``."""

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
