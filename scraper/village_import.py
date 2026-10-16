"""Import the pirateIB Village biology question bank into this app's format.

    python -m scraper.village_import convert --input capture.json --images imgs/
    python -m scraper.village_import convert --input capture.json --images imgs/ --merge seed/additional.json

How it works
------------
``village.pirateib.su`` is a single-page app whose question data arrives
AES-encrypted and is decrypted in the browser, so the plaintext never appears in
a static file. The capture step therefore runs the real page in a headless
browser and records the payloads the app itself hands to ``JSON.parse``:

    # capture.json is a list of {url, payload} records
    #   payload = {"questions": [...], "topics": {...}}

Only Paper 1 questions (one mark, four options) are converted, because those are
the ones this app can mark automatically and faithfully. Every question keeps a
``source`` of ``village:<reference>`` so provenance survives the merge.

Images referenced by a question are copied next to the app as ``image_path``
files, deduplicated by content hash, so the bank stays self-contained instead of
hot-linking to the upstream CDN.

Only material you are entitled to use should be imported. Check the source's
terms before redistributing anything this tool produces.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import unicodedata
from pathlib import Path

#: 2024 programme: village subtopic slug -> catalogue slug used by app/topics.py.
TOPICS_2024 = {
    "cell-biology": "cell-biology",
    "molecular-biology": "molecular-biology",
    "genetics": "genetics",
    "ecology": "ecology",
    "evolution-and-biodiversity": "evolution-biodiversity",
    "human-physiology": "human-physiology",
    "nucleic-acids": "nucleic-acids",
    "metabolism-cell-respiration-and-photosynthesis": "metabolism-cell-respiration-photosynthesis",
    "plant-biology": "plant-biology",
    "genetics-and-evolution": "genetics",
    "animal-physiology": "human-physiology",
    "ecology-and-conservation": "ecology",
}

#: 2025 programme: subtopic slugs from app/topics.py, title used for matching.
TOPICS_2025 = {
    "bio-a11-water": "Water", "bio-a12-nucleic-acids": "Nucleic Acids",
    "bio-a21-origins-of-cells-hl": "Origins of Cells", "bio-a22-cell-structure": "Cell Structure",
    "bio-a23-viruses-hl": "Viruses", "bio-a31-diversity-of-organisms": "Diversity of Organisms",
    "bio-a32-classification-and-cladistics-hl": "Classification and Cladistics",
    "bio-a41-evolution-and-speciation": "Evolution and Speciation",
    "bio-a42-conservation-of-biodiversity": "Conservation of Biodiversity",
    "bio-b11-carbohydrates-and-lipids": "Carbohydrates and Lipids", "bio-b12-proteins": "Proteins",
    "bio-b21-membranes-and-membrane-transport": "Membranes and Membrane Transport",
    "bio-b22-organelles-and-compartmentalization": "Organelles and Compartmentalization",
    "bio-b23-cell-specialization": "Cell Specialization", "bio-b31-gas-exchange": "Gas Exchange",
    "bio-b32-transport": "Transport", "bio-b33-muscle-and-motility-hl": "Muscle and Motility",
    "bio-b41-adaptation-to-environment": "Adaptation to Environment",
    "bio-b42-ecological-niches": "Ecological Niches",
    "bio-c11-enzymes-and-metabolism": "Enzymes and Metabolism",
    "bio-c12-cell-respiration": "Cell Respiration", "bio-c13-photosynthesis": "Photosynthesis",
    "bio-c21-chemical-signalling-hl": "Chemical Signalling", "bio-c22-neural-signalling": "Neural Signalling",
    "bio-c31-integration-of-body-systems": "Integration of Body Systems",
    "bio-c32-defense-against-disease": "Defence against Disease",
    "bio-c41-populations-and-communities": "Populations and Communities",
    "bio-c42-transfer-of-energy-and-matter": "Transfer of Energy and Matter",
    "bio-d11-dna-replication": "DNA Replication", "bio-d12-protein-synthesis": "Protein Synthesis",
    "bio-d13-mutations-and-gene-editing": "Mutations and Gene Editing",
    "bio-d21-cell-and-nuclear-division": "Cell and Nuclear Division",
    "bio-d22-gene-expression-hl": "Gene Expression", "bio-d23-water-potential": "Water Potential",
    "bio-d31-reproduction": "Reproduction", "bio-d32-inheritance": "Inheritance",
    "bio-d33-homeostasis": "Homeostasis", "bio-d41-natural-selection": "Natural Selection",
    "bio-d42-stability-and-change": "Stability and Change",
    "bio-d43-climate-change": "Climate Change",
}

_OPTION = re.compile(r"^\s*[-\s]*([A-D])[\.\)]\s*(.+?)\s*$", re.M)
_OPTION_TABLE = re.compile(r"^\s*\|\s*(?:\$[^|]*?\$)?\s*([A-D])[\.\)]\s*\|\s*(.+?)\s*\|", re.M)
_ANSWER = re.compile(r":answer\[\s*\*\*([A-D])\*\*\s*\]")
_STEM_END = re.compile(r"^\s*[-\s]*A[\.\)]", re.M)
_IMAGE_MARKER = re.compile(r"!\[[^\]]*\]\((?:question|image):")
_NEEDS_FIGURE = re.compile(
    r"\[diagram\]|illustration|figure|diagram below|shown below|graph below|"
    r"image above|pictured|photograph",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"(?:\bRetrieved\b|\bAdapted from\b|\bCopyright\b|\bWikimedia\b|"
    r"\bCreative Commons\b|\bCC-BY|\bSource:|https?://)",
    re.I,
)
_DIRECTIVE = re.compile(r"^\s*::+[a-zA-Z]*.*$", re.M)
_TABLE_OPTIONS = re.compile(r":::?tableoptions\{[^}]*\}", re.I)

#: LaTeX/HTML shrapnel from the upstream markdown dialect -> readable text.
_INLINE_FIXES = (
    (r"\\hspace\{[^}]*\}", " "), (r"\\checkmark", "\u2713"), (r"\\times", "\u00d7"),
    (r"\\ce\{([^}]*)\}", r"\1"), (r"\\textrm\{([^}]*)\}", r"\1"), (r"\\text\{([^}]*)\}", r"\1"),
    (r"\\underline\{([^}]*)\}", r"\1"), (r"\\mathrm\{([^}]*)\}", r"\1"),
    (r"\\mathbf\{([^}]*)\}", r"\1"), (r"\\\[", ""), (r"\\\]", ""), (r"\\\(", ""), (r"\\\)", ""),
    (r"\$", ""), (r"\\,", " "), (r"\\;", " "), (r"\\ ", " "), (r"\\(?:left|right)\.?", ""),
)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _pick_2025(subtopic: str, stem: str) -> str:
    """Best 2025 topic for a question: subtopic title first, stem as tie-break."""
    haystack = f"{subtopic} {stem}"
    best, best_score = None, 0
    for slug, title in TOPICS_2025.items():
        words = _plain(title).split()
        score = 2 * sum(3 if " " in word else 1 for word in words if word in _plain(subtopic))
        score += sum(1 for word in words if word in _plain(haystack))
        if score > best_score:
            best, best_score = slug, score
    return best or "bio-a22-cell-structure"


def clean(text, *, keep_newlines: bool = True) -> str:
    """Turn the upstream markdown/LaTeX/directive soup into readable text."""
    if not text:
        return ""
    value = str(text)
    value = re.sub(r"!\[[^\]]*\]\((?:question|image):[^)]*\)", " [diagram] ", value)
    value = re.sub(r":marks\[[^\]]*\]", "", value)
    value = re.sub(r":answer\[([^\]]*)\]", r"\1", value)
    value = re.sub(r":span\[([^\]]*)\](?:\{[^}]*\})?", r"\1", value)
    value = re.sub(r":br\b", " ", value)
    value = _TABLE_OPTIONS.sub(" ", value)
    value = _DIRECTIVE.sub(" ", value)
    value = re.sub(r"^\s*\|.*\|\s*$", " ", value, flags=re.M)
    value = re.sub(r'style="[^"]*"', " ", value)
    value = re.sub(r"©\s*Revision Village[^\n]*", " ", value)
    value = re.sub(r"Created with BioRender\.com\.?", " ", value)
    value = re.sub(r"^\s*Source:.*$", " ", value, flags=re.M | re.I)
    value = re.sub(r"^\s*(?:Adapted from|Retrieved|Wikimedia|CC-BY[^\n]*)\s*$", " ", value, flags=re.M)
    value = re.sub(r"\bRevision Village\b[^\n]*", " ", value)
    value = re.sub(r"<NO LINK>", "", value)
    value = re.sub(r"https?://\S+", " ", value)
    for pattern, replacement in _INLINE_FIXES:
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", value)
    value = re.sub(r"\\[a-zA-Z]+", " ", value)
    value = re.sub(r"[{}]", " ", value)
    value = re.sub(r"\\", " ", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"N_2", "N2", value)
    value = re.sub(r"\b[\w\-()]+\.(?:svg|png|jpe?g|gif)\b\.?", " ", value, flags=re.I)
    value = re.sub(
        r"^.*(?:Copyright under|Wikimedia Commons|Creative Commons|CC-BY[\w\-.]*).*$",
        " ", value, flags=re.M | re.I,
    )
    value = re.sub(r"^\s*(?:File|Image):.*$", " ", value, flags=re.M | re.I)
    # bullets left dangling once their table was stripped
    value = re.sub(r"(?m)^\s*[-*+•]\s*$", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value if keep_newlines else re.sub(r"\s+", " ", value).strip()


def _strip_trailing_attribution(text: str) -> str:
    match = _ATTRIBUTION.search(text)
    if match and match.start() > 20:
        text = text[: match.start()]
    text = re.sub(r"\s{2,}", " ", text).strip()
    return re.sub(r"[\s.\-–—,;:]+$", "", text).strip()


def _index_images(image_dir: Path) -> dict[str, str]:
    """Map each local image file onto its static path, deduplicated by hash."""
    index: dict[str, str] = {}
    for path in sorted(image_dir.glob("*")):
        if not path.is_file() or path.stat().st_size < 200:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        index[str(path)] = f"img/questions/{digest[:16]}.png"
    return index


def convert(records: list[dict], image_dir: Path) -> tuple[list[dict], collections.Counter]:
    """Convert captured village payloads into importable question dicts."""
    image_index = _index_images(image_dir)
    seen: set[str] = set()
    questions: list[dict] = []
    stats: collections.Counter = collections.Counter()

    for record in records:
        payload = record.get("payload")
        if not payload:
            continue
        parts = str(record.get("url", "")).split("/")
        level = parts[2] if len(parts) > 4 else ""
        subtopic_slug = parts[4] if len(parts) > 4 else ""
        data = json.loads(payload)
        subtopics = {t["id"]: t for t in data.get("topics", {}).get("subtopics", [])}

        for question in data.get("questions", []):
            qid = question.get("id")
            if not qid or qid in seen:
                continue
            seen.add(qid)

            if question.get("maximumMark") != 1:
                stats["odrzucone: nie 1 punkt"] += 1
                continue
            if (question.get("paper") or {}).get("reference") != "Paper 1":
                stats["odrzucone: nie Paper 1"] += 1
                continue
            answer = _ANSWER.search(question.get("markScheme") or "")
            if not answer:
                stats["odrzucone: brak litery odpowiedzi"] += 1
                continue
            letter = answer.group(1)

            content = question.get("content") or ""
            pairs = _OPTION.findall(content)
            if len(pairs) < 4:
                pairs = _OPTION_TABLE.findall(content)
            if len(pairs) < 4:
                stats["odrzucone: brak czterech opcji"] += 1
                continue
            options = {label: clean(text, keep_newlines=False) for label, text in pairs}
            options = {label: text for label, text in options.items() if text}
            if letter not in options or len(options) != 4:
                stats["odrzucone: niekompletne opcje"] += 1
                continue
            if len({text.strip().lower() for text in options.values()}) != 4:
                stats["odrzucone: opcje nierozróżnialne"] += 1
                continue

            stem = clean(_STEM_END.split(content, maxsplit=1)[0])
            if len(stem) < 15:
                stats["odrzucone: za krótki rdzeń"] += 1
                continue

            # Only images that belong to the stem: options that are themselves
            # pictures would need a per-choice schema this app does not have.
            first_option = None
            for pattern in (_OPTION, _OPTION_TABLE):
                match = pattern.search(content)
                if match and (first_option is None or match.start() < first_option):
                    first_option = match.start()
            markers = [m.start() for m in _IMAGE_MARKER.finditer(content)]
            stem_markers = [p for p in markers if first_option is None or p < first_option]
            if question.get("images") and not stem_markers:
                stats["odrzucone: obrazek należy do opcji"] += 1
                continue
            image_files = []
            for image in question.get("images") or []:
                path = image.get("path") or ""
                local = image_dir / f"{path.split('/')[2]}_{path.split('/')[-1]}" if path else None
                static = image_index.get(str(local)) if local else None
                if static:
                    image_files.append(static)
            if question.get("images") and not image_files:
                stats["odrzucone: obrazka nie udało się pobrać"] += 1
                continue
            if image_files:
                stats["z obrazkiem"] += 1
                stem = stem.replace("[diagram]", " ")
                stem = re.sub(
                    r"^\s*(?:Magnification|Scale bar|Source)[^\n]*$", " ", stem, flags=re.M | re.I
                )
                stem = _strip_trailing_attribution(stem)
                if len(stem) < 15:
                    stats["odrzucone: sam nagłówek po usunięciu szumu"] += 1
                    continue
            elif _NEEDS_FIGURE.search(stem):
                stats["odrzucone: rdzeń wymaga rysunku"] += 1
                continue

            choices = [options[label] for label in sorted(options)]
            explanation = clean(_ANSWER.sub("", question.get("markScheme") or ""))
            explanation = re.sub(r"^\s*Explanation\s*:?", "", explanation, flags=re.I).strip()
            explanation = re.sub(r"^[\s\\]+", "", explanation).strip()
            explanation = re.sub(r"!?\[[^\]]*\]\((?:question|image):[^)]*\)", " ", explanation)
            explanation = explanation.replace("[diagram]", " ")
            match = _ATTRIBUTION.search(explanation)
            if match and match.start() > 20:
                explanation = explanation[: match.start()]
            explanation = re.sub(r"\s{2,}", " ", explanation).strip()

            subtopic = subtopics.get(question.get("subtopicId")) or {}
            if level.endswith("2025"):
                topic = _pick_2025(subtopic.get("name") or subtopic_slug, stem)
            else:
                topic = TOPICS_2024.get(subtopic_slug, "cell-biology")

            difficulty = ((question.get("difficulty") or {}).get("difficultyLevel") or "medium").lower()
            if difficulty not in {"easy", "medium", "hard"}:
                difficulty = "medium"

            tags = ["village", "ib-biology", "paper-1", "multiple-choice"]
            tags.append("2025-syllabus" if level.endswith("2025") else "2024-syllabus")
            tags.append("hl" if level.startswith("hl") else "sl")
            if subtopic.get("code"):
                tags.append(str(subtopic["code"]).lower())
            if image_files:
                tags.append("with-diagram")

            questions.append(
                {
                    "topic": topic,
                    "type": "multiple_choice",
                    "question_text": stem,
                    "correct_answer": options[letter],
                    "choices": choices,
                    "marks": 1,
                    "explanation": explanation[:1500],
                    "tags": tags,
                    "difficulty": difficulty,
                    "source": f"village:{question['reference']}",
                    "image_path": image_files[0] if image_files else None,
                }
            )
            stats[f"przyjęte: {topic}"] += 1

    return questions, stats


def _merge(path: Path, questions: list[dict]) -> tuple[int, int]:
    """Append converted questions to an existing seed file; return (before, after)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    bank = payload.setdefault("questions", [])
    before = len(bank)
    bank.extend(questions)
    payload.setdefault("meta", {})["count"] = len(bank)
    payload["meta"]["village_import"] = {
        "source": "pirateIB Village (village.pirateib.su) — Paper 1 questions",
        "added": len(questions),
        "with_diagrams": sum(1 for q in questions if q.get("image_path")),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return before, len(bank)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("convert", help="convert a capture file into importable questions")
    run.add_argument("--input", type=Path, required=True, help="capture file: list of {url, payload}")
    run.add_argument("--images", type=Path, required=True, help="directory of downloaded images")
    run.add_argument("--output", type=Path, default=Path("seed/village.json"), help="where to write the result")
    run.add_argument("--merge", type=Path, default=None, help="append the result to this seed file instead")

    args = parser.parse_args(argv)
    if args.command != "convert":
        parser.error("unknown command")

    records = json.loads(args.input.read_text(encoding="utf-8"))
    questions, stats = convert(records, args.images)

    print(f"przyjęte: {len(questions)}")
    for key in sorted(stats):
        if key.startswith("odrzucone"):
            print(f"  {key}: {stats[key]}")
    print(f"  pytań z obrazkiem: {sum(1 for q in questions if q.get('image_path'))}")

    if args.merge:
        before, after = _merge(args.merge, questions)
        print(f"scalono z {args.merge}: {before} -> {after} pytań")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"zapisano {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
