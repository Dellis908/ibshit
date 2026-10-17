"""Tests for the chemistry bank: catalogue, grouping and import."""

from __future__ import annotations

import json

from app.extensions import db
from app.models import Question, Topic
from app.topics import SUBJECTS, chemistry_topics, subject_groups


def test_chemistry_catalogue_is_loaded():
    topics = chemistry_topics()
    assert len(topics) >= 25
    refs = [topic["ref"] for topic in topics]
    assert "Structure 1.1" in refs
    assert all(topic["slug"].startswith("chem-") for topic in topics)
    # Slugs must be unique or the import would merge topics silently.
    assert len({topic["slug"] for topic in topics}) == len(topics)


def test_chemistry_is_grouped_by_theme_in_syllabus_order():
    headings = [heading for heading, _topics in subject_groups("chemistry")]
    assert headings[0].startswith("Structure 1")
    assert headings[1].startswith("Structure 2")
    assert headings[2].startswith("Structure 3")
    assert headings[3].startswith("Reactivity 1")
    assert headings[-1] in {"Tools", "Inquiry"} or headings[-2:] == ["Tools", "Inquiry"]


def test_both_subjects_are_declared():
    assert set(SUBJECTS) == {"biology", "chemistry"}


def test_chemistry_import_creates_topics_and_questions(app):
    from app.content import import_file, sync_topics

    with app.app_context():
        sync_topics()
        db.session.commit()
        before = db.session.query(Question).count()
        report = import_file("seed/chemistry.json")
        db.session.commit()

        assert report.created > 500, report.summary()
        assert db.session.query(Question).count() > before

        chemistry = db.session.query(Topic).filter_by(subject="chemistry").all()
        assert chemistry, "chemistry topics should exist"
        assert all(topic.slug.startswith("chem-") for topic in chemistry)
        # The import must not have filed chemistry questions under biology.
        bio_slugs = {topic.slug for topic in db.session.query(Topic).filter_by(subject="biology")}
        chem_slugs = {topic.slug for topic in chemistry}
        assert not (bio_slugs & chem_slugs)


def test_chemistry_questions_are_answerable(app):
    """Every imported question must have an answer the engine can compare."""
    from app.content import import_file, sync_topics

    with app.app_context():
        sync_topics()
        db.session.commit()
        import_file("seed/chemistry.json")
        db.session.commit()

        rows = (
            db.session.query(Question)
            .join(Topic)
            .filter(Topic.subject == "chemistry")
            .limit(400)
            .all()
        )
        assert rows
        for question in rows:
            assert question.correct_answer.strip()
            if question.question_type == "multiple_choice":
                choices = json.loads(question.choices_json or "[]")
                assert len(choices) == 4
                assert question.correct_answer in choices


def test_topics_page_lists_chemistry(client):
    body = client.get("/topics").get_data(as_text=True)
    assert "Chemistry" in body
    assert "Structure 1" in body
