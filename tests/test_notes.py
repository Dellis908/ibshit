"""Tests for the topic notes: filename mapping and the serving route."""

from __future__ import annotations

import json

import pytest

from app.extensions import db
from app.models import Question, Topic
from app.notes import build_manifest, load_manifest, notes_by_topic, normalise, topic_for_note


# ------------------------------------------------------------------- mapping

@pytest.mark.parametrize(
    "stem,expected",
    [
        # The notes name files after the subtopic, without the catalogue prefix.
        ("water", "bio-a11-water"),
        ("dna-replication", "bio-d11-dna-replication"),
        ("gas-exchange", "bio-b31-gas-exchange"),
        ("cell-&-nuclear-division", "bio-d21-cell-and-nuclear-division"),
        ("mutations-&-gene-editing", "bio-d13-mutations-and-gene-editing"),
        # British spellings in the filenames, American in the slugs.
        ("cell-specialisation", "bio-b23-cell-specialization"),
        ("organelles-&-compartmentalisation", "bio-b22-organelles-and-compartmentalization"),
        ("defence-against-disease", "bio-c32-defense-against-disease"),
        # D4.2 is "Stability and Change"; the catalogue slug was once wrong.
        ("stability-&-change", "bio-d42-stability-and-change"),
        # The skills notes have no subtopic of their own.
        ("tool-1--experimental-techniques", "data-analysis"),
        ("tool-3--mathematics", "data-analysis"),
    ],
)
def test_note_filenames_map_to_catalogue_slugs(stem, expected):
    assert topic_for_note(stem) == expected


@pytest.mark.parametrize("stem", ["something-else", "", "unit-9-nonsense"])
def test_unknown_note_names_are_not_guessed(stem):
    assert topic_for_note(stem) is None


def test_normalise_accepts_both_shapes():
    """A slug and its note filename reduce to the same value."""
    assert normalise("bio-b21-membranes-and-membrane-transport") == normalise(
        "membranes-&-membrane-transport"
    )
    assert normalise("bio-a21-origins-of-cells-hl") == normalise("origins-of-cells")


# ------------------------------------------------------------------ manifest

def test_missing_manifest_reads_as_no_notes(tmp_path):
    assert load_manifest(tmp_path) == []
    assert notes_by_topic(tmp_path) == {}


def test_broken_manifest_reads_as_no_notes(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "manifest.json").write_text("{not json", encoding="utf-8")
    assert load_manifest(tmp_path) == []


def test_notes_are_grouped_by_topic_and_level(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    payload = build_manifest(
        [
            {"topic": "bio-a11-water", "level": "SL", "stem": "water", "file": "bio-a11-water/SL-water.pdf", "title": "SL"},
            {"topic": "bio-a11-water", "level": "HL", "stem": "water", "file": "bio-a11-water/HL-water.pdf", "title": "HL"},
            {"topic": "bio-b32-transport", "level": "HL", "stem": "transport", "file": "bio-b32-transport/HL-transport.pdf", "title": "T"},
        ]
    )
    (notes_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    grouped = notes_by_topic(tmp_path)
    assert set(grouped) == {"bio-a11-water", "bio-b32-transport"}
    # HL before SL, so the fuller notes come first.
    assert [note["level"] for note in grouped["bio-a11-water"]] == ["HL", "SL"]


# --------------------------------------------------------------------- route

@pytest.fixture
def notes_app(app, tmp_path):
    """The app pointed at a notes folder holding one real file."""
    data_dir = tmp_path / "data"
    (data_dir / "notes" / "bio-a11-water").mkdir(parents=True)
    pdf = data_dir / "notes" / "bio-a11-water" / "HL-water.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfake\n")
    manifest = build_manifest(
        [
            {
                "topic": "bio-a11-water",
                "level": "HL",
                "stem": "water",
                "file": "bio-a11-water/HL-water.pdf",
                "title": "A1.1 Water — HL",
                "bytes": pdf.stat().st_size,
            }
        ]
    )
    (data_dir / "notes" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app.config["DATA_DIR"] = data_dir
    app.config["NOTES_DIR"] = data_dir / "notes"
    with app.app_context():
        # A topic page redirects away when the topic has no questions.
        topic = db.session.query(Topic).filter_by(slug="bio-a11-water").one_or_none()
        if topic is None:
            topic = Topic(slug="bio-a11-water", title="A1.1 Water", position=0)
            db.session.add(topic)
            db.session.flush()
        if not topic.questions:
            db.session.add(
                Question(
                    topic_id=topic.id,
                    question_text="Which property of water supports transpiration?",
                    question_type="multiple_choice",
                    correct_answer="Cohesion",
                    choices_json=json.dumps(["Cohesion", "Adhesion", "Density", "Solvency"]),
                    marks=1,
                    difficulty="easy",
                    explanation="",
                    tags_json="[]",
                    source="notes-test",
                    fingerprint="notes-test-fingerprint",
                )
            )
        db.session.commit()
    return app


def test_notes_route_serves_the_pdf(notes_app):
    client = notes_app.test_client()
    _login(client, notes_app)
    response = client.get("/notes/bio-a11-water/HL")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_notes_route_rejects_unknown_topic_level_and_file(notes_app):
    client = notes_app.test_client()
    _login(client, notes_app)
    assert client.get("/notes/bio-a11-water/XX").status_code == 404   # no such level
    assert client.get("/notes/bio-b32-transport/HL").status_code == 404  # not in manifest
    assert client.get("/notes/../../etc/passwd/HL").status_code == 404


def test_notes_route_404s_when_the_file_was_never_installed(notes_app):
    """The manifest promises a file; a deployment without it must say 404."""
    (notes_app.config["NOTES_DIR"] / "bio-a11-water" / "HL-water.pdf").unlink()
    client = notes_app.test_client()
    _login(client, notes_app)
    assert client.get("/notes/bio-a11-water/HL").status_code == 404


def test_topic_page_offers_the_notes(notes_app):
    client = notes_app.test_client()
    _login(client, notes_app)
    body = client.get("/topics/bio-a11-water").get_data(as_text=True)
    assert "Notes" in body
    assert "/notes/bio-a11-water/HL" in body


def _login(client, app) -> None:
    """Sign in as the fixture account through the real form."""
    import re

    page = client.get("/auth/login").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    client.post(
        "/auth/login",
        data={"email": "student@example.com", "password": "correct-horse-battery", "csrf_token": token},
        follow_redirects=True,
    )
