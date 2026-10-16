"""Topic notes: the PDF study notes that ship alongside the question bank.

The notes are revision guides authored per syllabus subtopic, one file per level
(HL and SL). They live outside the repository — they are hundreds of megabytes —
so the schema here is small: a manifest maps a topic slug and a level onto one
file, and the files themselves sit in ``NOTES_DIR``.

Why a manifest instead of scanning the directory: the directory may legitimately
be empty (a fresh clone that has not installed the notes), and scanning it would
then make every topic look as though it has no notes rather than "notes not
installed here". The manifest says what *should* be there; the route checks the
disk before serving.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.topics import NEW_SYLLABUS_TOPICS

#: Level labels, in the order they are offered for a topic.
LEVELS: tuple[str, ...] = ("HL", "SL")

#: Note filename stems that differ from the catalogue slug only by spelling.
#: The notes use British spellings; the catalogue slugs follow the guide's
#: American ones.
_ALIASES: dict[str, str] = {
    "cell-specialisation": "cell-specialization",
    "organelles-and-compartmentalisation": "organelles-and-compartmentalization",
    "defence-against-disease": "defense-against-disease",
    # D4.2 is "Stability and Change" (the catalogue slug was once wrong).
    "stability-and-change": "stability-and-change",
    "sustainability-and-change": "stability-and-change",
}

#: The three "Tool" notes have no subtopic of their own; they are the skills
#: material, which the catalogue keeps under the data-analysis topic.
TOOL_SLUG = "data-analysis"
_TOOL_PREFIX = "tool-"


def normalise(name: str) -> str:
    """Reduce a note filename or a topic slug to a comparable tail.

    ``"bio-b21-membranes-and-membrane-transport"`` and
    ``"membranes-&-membrane-transport"`` both become
    ``"membranes-and-membrane-transport"``.
    """
    text = str(name or "").lower().replace("&", " and ")
    text = text.replace("--", "-")
    text = re.sub(r"^bio-[a-z]\d+\.?\d*-?", "", text)
    text = re.sub(r"-(hl|sl)$", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


#: Comparable tail -> catalogue slug, built once from the 2025 syllabus.
_TOPIC_BY_TAIL: dict[str, str] = {normalise(spec.slug): spec.slug for spec in NEW_SYLLABUS_TOPICS}


def topic_for_note(name: str) -> str | None:
    """Catalogue slug for a note filename, or ``None`` when nothing matches."""
    tail = normalise(name)
    tail = _ALIASES.get(tail, tail)
    if tail.startswith(_TOOL_PREFIX):
        return TOOL_SLUG
    return _TOPIC_BY_TAIL.get(tail)


def manifest_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "notes" / "manifest.json"


def load_manifest(data_dir: str | Path) -> list[dict]:
    """Read the notes manifest; an absent or unreadable file reads as empty."""
    path = manifest_path(data_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    notes = payload.get("notes") if isinstance(payload, dict) else payload
    if not isinstance(notes, list):
        return []
    return [note for note in notes if isinstance(note, dict) and note.get("topic") and note.get("file")]


def notes_by_topic(data_dir: str | Path) -> dict[str, list[dict]]:
    """Manifest grouped by topic slug, levels in :data:`LEVELS` order."""
    grouped: dict[str, list[dict]] = {}
    for note in load_manifest(data_dir):
        grouped.setdefault(note["topic"], []).append(note)
    for notes in grouped.values():
        notes.sort(
            key=lambda note: (
                LEVELS.index(note["level"]) if note.get("level") in LEVELS else len(LEVELS),
                str(note.get("stem") or note.get("file") or ""),
            )
        )
    return grouped


def build_manifest(entries: list[dict]) -> dict:
    """Serialise manifest entries in a stable order, for committing to git."""
    ordered = sorted(
        entries,
        key=lambda n: (
            n["topic"],
            LEVELS.index(n["level"]) if n.get("level") in LEVELS else 9,
            str(n.get("stem") or n.get("file") or ""),
        ),
    )
    return {"version": 1, "count": len(ordered), "notes": ordered}
