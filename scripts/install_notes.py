#!/usr/bin/env python3
"""Install the topic notes into ``data/notes/`` and write the manifest.

    python scripts/install_notes.py --source "/Volumes/SSD/bio"
    python scripts/install_notes.py --source "/Volumes/SSD/bio" --dry-run

The notes are revision PDFs authored per syllabus subtopic, one file per level
(HL and SL), organised as ``<source>/Biology <LEVEL> 2025/<Theme>/<name>.pdf``.
They are far too large to keep in the repository, so this script copies them next
to the app and records what it installed in ``data/notes/manifest.json`` — that
manifest is small enough to commit, and it tells the app (and a fresh clone)
which notes exist.

Filenames are matched to catalogue slugs via :mod:`app.notes`; anything that
cannot be matched is reported and skipped rather than guessed at.

Run it again after adding notes: it is incremental and safe to repeat.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.notes import LEVELS, build_manifest, topic_for_note  # noqa: E402
from app.topics import NEW_SYLLABUS_BY_SLUG  # noqa: E402

#: ``Biology HL 2025`` -> ``HL``
_LEVEL_DIR = re.compile(r"biology\s+(HL|SL)\s+\d{4}", re.I)


def iter_pdfs(source: Path):
    """Yield ``(level, theme, stem, path)`` for every real note PDF.

    macOS writes an AppleDouble sidecar (``._name.pdf``) next to every file on a
    non-native filesystem; those are resource forks, not notes.
    """
    for path in sorted(source.rglob("*.pdf")):
        if path.name.startswith("._"):
            continue
        match = next((_LEVEL_DIR.search(part) for part in path.parts if _LEVEL_DIR.search(part)), None)
        if match is None:
            continue
        level = match.group(1).upper()
        theme = path.parent.name
        yield level, theme, path.stem, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="folder holding the notes (e.g. /Volumes/SSD/bio)")
    parser.add_argument("--dest", type=Path, default=ROOT / "data" / "notes", help="where to install them")
    parser.add_argument("--dry-run", action="store_true", help="report the mapping without copying anything")
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        parser.error(f"not a directory: {args.source}")

    notes: list[dict] = []
    unmatched: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for level, theme, stem, path in iter_pdfs(args.source):
        topic = topic_for_note(stem)
        if topic is None:
            unmatched.append((level, stem))
            continue
        # A topic can legitimately carry more than one note per level — the three
        # "Tool" notes all belong to data-analysis — so the identity is the
        # filename, not the (topic, level) pair.
        key = (topic, level, stem)
        if key in seen:
            unmatched.append((level, f"{stem} (seen twice for {topic})"))
            continue
        seen.add(key)

        spec = NEW_SYLLABUS_BY_SLUG.get(topic)
        if stem.startswith("tool-"):
            # "tool-2--technology" -> "Tool 2 — Technology"
            rest = re.sub(r"^tool-(\d)--", "", stem).replace("-", " ")
            number = re.match(r"tool-(\d)", stem).group(1)
            title = f"Tool {number} — {rest.capitalize()} — {level}"
        elif spec is not None:
            title = f"{spec.full_title} — {level}"
        else:
            # The skills notes live under the 2024 data-analysis topic.
            title = f"{stem.replace('-', ' ').capitalize()} — {level}"
        rel = Path(topic) / f"{level}-{stem}.pdf"
        notes.append(
            {
                "topic": topic,
                "level": level,
                "stem": stem,
                "title": title,
                "theme": theme,
                "file": rel.as_posix(),
                "bytes": path.stat().st_size,
                "_source": path,
            }
        )

    print(f"znaleziono notatek: {len(notes)}")
    per_topic: dict[str, list[str]] = {}
    for note in notes:
        per_topic.setdefault(note["topic"], []).append(note["level"])
    print(f"tematów pokrytych:   {len(per_topic)}")
    both = sum(1 for levels in per_topic.values() if set(levels) == set(LEVELS))
    print(f"  z HL i SL:         {both}")
    print(f"  tylko jeden poziom: {len(per_topic) - both}")
    total = sum(note["bytes"] for note in notes)
    print(f"rozmiar:             {total / 1e6:.1f} MB")

    if unmatched:
        print(f"\nNIE DOPASOWANO ({len(unmatched)}):")
        for level, stem in unmatched:
            print(f"   [{level}] {stem}")

    if args.dry_run:
        print("\n--dry-run: nic nie skopiowano")
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for note in notes:
        target = args.dest / note["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        source: Path = note.pop("_source")
        if target.exists() and target.stat().st_size == note["bytes"]:
            skipped += 1
            continue
        shutil.copy2(source, target)
        copied += 1

    manifest = build_manifest([{k: v for k, v in note.items() if k != "_source"} for note in notes])
    (args.dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"\nskopiowano: {copied} | pominięto (już aktualne): {skipped}")
    print(f"manifest:   {args.dest / 'manifest.json'}")
    print("\nUwaga: samo skopiowanie nie wystarczy na serwerze — pliki i manifest")
    print("trzeba też wgrać na VPS (patrz DEPLOY.md, sekcja 'Notatki').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
