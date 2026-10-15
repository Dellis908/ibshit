"""Turn parse results into an import-ready JSON payload.

The output is written as a plain JSON list of question objects, which is exactly
what ``flask quiz import`` accepts. Every question keeps a ``_confidence`` score
and ``_warnings`` list so you can review the parse before trusting it: the
``flask quiz import`` command ignores keys it does not recognise.

This module is shared by the scraper CLI and the pytest suite.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from scraper import pdf_parser
from scraper.pdf_parser import ParsedQuestion


@dataclass
class BuildReport:
    """Summary of a build run."""

    total: int = 0
    by_topic: dict[str, int] | None = None
    by_type: dict[str, int] | None = None
    low_confidence: int = 0
    confidence_threshold: float = 0.6

    def __post_init__(self) -> None:
        self.by_topic = self.by_topic or {}
        self.by_type = self.by_type or {}

    def summary(self) -> str:
        lines = [f"questions: {self.total}"]
        if self.by_type:
            lines.append("by type:")
            for key, value in sorted(self.by_type.items()):
                lines.append(f"  {key}: {value}")
        if self.by_topic:
            lines.append("by topic:")
            for key, value in sorted(self.by_topic.items()):
                lines.append(f"  {key}: {value}")
        lines.append(
            f"low confidence (< {self.confidence_threshold}): {self.low_confidence} "
            "— review these before trusting them"
        )
        return "\n".join(lines)


def topic_from_filename(name: str) -> str | None:
    """Guess a syllabus topic from a file name, e.g. ``cell_biology_2021.pdf``.

    The text is handed to the app's own topic normaliser when available, which
    keeps the mapping consistent with the rest of the project.
    """
    stem = Path(name).stem
    # Drop years, paper numbers and other noise.
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    stem = re.sub(r"\b(paper|qp|ms|markscheme|marking|hl|sl|dp|topic|questions?)\b", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_\-.]+", " ", stem).strip()

    try:
        from app.topics import normalize_topic

        return normalize_topic(stem)
    except Exception:
        # The app is not importable from the scraper environment; fall back to
        # the slug shape used by the question bank.
        slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
        return slug or None


#: Filename fragments that indicate a document is a mark scheme.
MARK_SCHEME_HINTS = (
    "markscheme", "mark-scheme", "mark_scheme", "marking", "ms", "answers",
    "answer-key", "answerkey", "solutions", "soln",
)

#: Noise words removed before comparing two filenames.
_FILENAME_NOISE = (
    "paper", "qp", "ms", "markscheme", "marking", "hl", "sl", "dp", "topic",
    "questions", "question", "answers", "solutions", "specimen", "may", "nov",
    "june", "october", "final", "exam",
)


def is_mark_scheme(name: str) -> bool:
    """Whether a filename looks like a mark scheme rather than a question paper."""
    stem = Path(name).stem.lower()
    tokens = re.split(r"[^a-z0-9]+", stem)
    for hint in MARK_SCHEME_HINTS:
        if hint in tokens or hint in stem:
            return True
    return False


def paper_key(name: str) -> str:
    """A comparable key so ``cell_2021_qp`` pairs with ``cell_2021_ms``."""
    stem = Path(name).stem.lower()
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", stem)
        if token and token not in _FILENAME_NOISE
    ]
    return " ".join(tokens)


def pair_papers_with_mark_schemes(texts: dict[str, str]) -> dict[str, str]:
    """Map each question paper's name to its mark scheme's text.

    Pairing is done on the cleaned filename: ``cell_biology_2021_qp`` pairs with
    ``cell_biology_2021_ms``. An unpaired mark scheme is ignored rather than
    guessed at.
    """
    schemes: dict[str, list[str]] = {}
    for name in texts:
        if is_mark_scheme(name):
            schemes.setdefault(paper_key(name), []).append(name)

    pairs: dict[str, str] = {}
    for name in texts:
        if is_mark_scheme(name):
            continue

        key = paper_key(name)
        candidates = schemes.get(key)

        if not candidates:
            # Fall back to a subset match, so "cell biology 2021 qp" still
            # pairs with "cell biology ms" when the year is only on one side.
            key_tokens = set(key.split())
            for scheme_key, scheme_names in schemes.items():
                scheme_tokens = set(scheme_key.split())
                if key_tokens and scheme_tokens and (
                    key_tokens <= scheme_tokens or scheme_tokens <= key_tokens
                ):
                    candidates = scheme_names
                    break

        if not candidates:
            continue
        # Prefer an unambiguous pairing; otherwise take the first candidate.
        pairs[name] = texts[candidates[0]]
    return pairs


def question_from_parsed(parsed: ParsedQuestion) -> dict | None:
    """Convert a :class:`ParsedQuestion` into importable JSON, or ``None``."""
    payload = parsed.to_dict()

    # A question without an answer is still useful as a worksheet prompt, but it
    # cannot be graded, so mark it clearly instead of importing a blank answer.
    if not payload.get("correct_answer"):
        payload["correct_answer"] = ""
        payload["_warnings"] = list(payload.get("_warnings") or []) + [
            "no answer found — supply one before importing"
        ]

    text = payload.get("question_text") or ""
    if len(text) < 15:
        return None
    return payload


def merge_answers(
    questions: list[ParsedQuestion], answers: dict[int, tuple[str, list[str]]]
) -> list[ParsedQuestion]:
    """Attach mark-scheme answers to parsed questions."""
    for question in questions:
        number = _question_number(question.source)
        if number is None or number not in answers:
            continue
        answer_text, warnings = answers[number]
        question.correct_answer = answer_text
        question.warnings.extend(warnings)
        if question.choices:
            resolved, resolve_warnings = pdf_parser.answer_from_choices(
                question.choices, answer_text
            )
            if resolved:
                question.correct_answer = resolved
                question.question_type = "multiple_choice"
            question.warnings.extend(resolve_warnings)
    return questions


def _question_number(source: str | None) -> int | None:
    if not source:
        return None
    match = re.search(r"(\d{1,2})", source)
    return int(match.group(1)) if match else None


def build_from_texts(
    texts: dict[str, str],
    *,
    topic_overrides: dict[str, str] | None = None,
    confidence_threshold: float = 0.6,
) -> tuple[list[dict], BuildReport]:
    """Build importable questions from ``{source_name: text}``."""
    topic_overrides = topic_overrides or {}
    report = BuildReport(confidence_threshold=confidence_threshold)
    output: list[dict] = []
    seen_text: set[str] = set()

    mark_schemes = pair_papers_with_mark_schemes(texts)

    for name, text in texts.items():
        # A mark scheme on its own is not a question source.
        if is_mark_scheme(name) and name not in mark_schemes:
            continue

        topic = topic_overrides.get(name) or topic_from_filename(name)
        answers = (
            pdf_parser.parse_mark_scheme(mark_schemes[name]) if name in mark_schemes else None
        )
        parsed = pdf_parser.parse_questions(text, topic=topic, source=name, answers=answers)
        for question in parsed:
            payload = question_from_parsed(question)
            if payload is None:
                continue
            fingerprint = re.sub(r"\W+", "", (payload["question_text"] or "").lower())[:200]
            if fingerprint in seen_text:
                continue
            seen_text.add(fingerprint)

            output.append(payload)
            report.total += 1
            slug = payload.get("topic") or "unknown"
            report.by_topic[slug] = report.by_topic.get(slug, 0) + 1
            qtype = payload.get("type") or "short_answer"
            report.by_type[qtype] = report.by_type.get(qtype, 0) + 1
            if payload.get("_confidence", 0) < confidence_threshold:
                report.low_confidence += 1

    return output, report


# ------------------------------------------------------------------------ I/O

def write_questions(questions: list[dict], path: str | Path) -> Path:
    """Write the payload as JSON (an array, ready for ``flask quiz import``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(questions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def write_importable(questions: list[dict], path: str | Path, *, skip_incomplete: bool = True) -> Path:
    """Write only the questions that can actually be graded."""
    usable = [
        q for q in questions if (q.get("correct_answer") or "").strip()
    ] if skip_incomplete else questions
    return write_questions(usable, path)


def write_review(questions: list[dict], path: str | Path) -> Path:
    """Write a human-readable review file for the low-confidence questions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Scraped questions needing review", ""]
    for index, question in enumerate(questions, start=1):
        warnings = question.get("_warnings") or []
        confidence = question.get("_confidence")
        lines.append(f"## {index}. [{question.get('topic')}] confidence={confidence}")
        lines.append("")
        lines.append(question.get("question_text", ""))
        lines.append("")
        lines.append(f"**Proposed answer:** {question.get('correct_answer') or '— none —'}")
        lines.append("")
        if warnings:
            lines.append("Warnings: " + "; ".join(warnings))
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
