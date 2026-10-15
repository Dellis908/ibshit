"""Turn extracted PDF text into importable question dictionaries.

IB-style papers are laid out in a very consistent way, which is what this
parser relies on:

* questions begin with a number at the start of a line (``1.``, ``12.``, ``3)``)
* marks are given in brackets at the end of a question part (``[2]``, ``(3 marks)``)
* command terms (``State``, ``Calculate``, ``Suggest``…) indicate the answer type

Real papers are messy, so every question carries a ``confidence`` score and a
``warnings`` list. Anything below the confidence threshold should be reviewed by
a human before it is trusted.

This module performs no I/O beyond reading the text files it is given and has no
third-party dependencies, so it can be unit tested with plain strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------- patterns

#: A new question starts with a number at the beginning of a line.
QUESTION_START_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s*(.*)$", re.MULTILINE)

#: Marks: "[2]", "(2 marks)", "[2 marks]", "2 marks".
MARKS_RE = re.compile(
    r"[\[(]\s*(\d{1,2})\s*(?:marks?|mks?)?\s*[\])]|(?<!\d)(\d{1,2})\s+marks?\b",
    re.IGNORECASE,
)

#: Multiple-choice options: "A  mitosis", "B) meiosis", "(C) ...".
OPTION_RE = re.compile(r"^\s*[(\[]?([A-D])[)\].:]?\s+(.+?)\s*$", re.MULTILINE)

#: Numbered sub-parts inside a question: "(a)", "a)", "(i)".
SUBPART_RE = re.compile(r"^\s*[(\[]?([a-h]|[ivx]{1,4})[)\]]\s+(.+?)\s*$", re.MULTILINE)

#: Lines that are page furniture rather than question content.
NOISE_RES = (
    re.compile(r"^\s*-?\s*\d{1,3}\s*-?\s*$"),  # bare page numbers
    re.compile(r"^\s*page\s+\d+", re.IGNORECASE),
    re.compile(r"^\s*(turn over|continued|end of (section|paper))\s*$", re.IGNORECASE),
    re.compile(r"^\s*(do not write|write your answers?|answer all|total)\b", re.IGNORECASE),
    re.compile(r"^\s*©.*$"),
    re.compile(r"^\s*(smearchive|pirateib|ib documents|savemyexams)\b", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*(?:minutes?|hours?)\b", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*(?:maximum mark|marks?)\s*:?\s*\d*\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*[\W_]{4,}\s*$"),  # rules made of punctuation
)

#: Command terms and the answer type they imply.
COMMAND_TERMS: dict[str, str] = {
    # calculation
    "calculate": "calculation",
    "determine": "calculation",
    "estimate": "calculation",
    "measure": "calculation",
    "work out": "calculation",
    # short answer (everything below is written prose)
    "state": "short_answer",
    "outline": "short_answer",
    "define": "short_answer",
    "list": "short_answer",
    "identify": "short_answer",
    "name": "short_answer",
    "give": "short_answer",
    "describe": "short_answer",
    "explain": "short_answer",
    "distinguish": "short_answer",
    "compare": "short_answer",
    "contrast": "short_answer",
    "suggest": "short_answer",
    "deduce": "short_answer",
    "discuss": "short_answer",
    "evaluate": "short_answer",
    "annotate": "short_answer",
    "label": "short_answer",
    "draw": "short_answer",
    # true/false
    "true or false": "true_false",
    "decide whether": "true_false",
}

#: Sort longest first so "calculate the" beats "calculate".
_TERM_ORDER = sorted(COMMAND_TERMS, key=len, reverse=True)


# ---------------------------------------------------------------------- model

@dataclass
class ParsedQuestion:
    """A question recovered from PDF text."""

    question_text: str
    correct_answer: str = ""
    question_type: str = "short_answer"
    marks: int = 1
    choices: list[str] = field(default_factory=list)
    explanation: str = ""
    topic: str | None = None
    source: str | None = None
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to the payload accepted by ``app.content.import_questions``."""
        data = {
            "topic": self.topic,
            "type": self.question_type if self.correct_answer else "short_answer",
            "question_text": self.question_text,
            "correct_answer": self.correct_answer,
            "marks": self.marks,
            "explanation": self.explanation,
            "tags": ["scraped"],
            "difficulty": "medium",
            "source": self.source,
            "_confidence": round(self.confidence, 3),
            "_warnings": self.warnings,
        }
        if self.question_type == "multiple_choice" and self.choices:
            data["choices"] = self.choices
        return data


# ------------------------------------------------------------------- cleaning

def clean_text(raw: str) -> str:
    """Tidy extracted PDF text: de-hyphenate, collapse spaces and blank lines."""
    if not raw:
        return ""

    text = raw.replace("\u00ad", "")  # soft hyphens
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2212", "-")  # unicode minus

    # Join words split across a line break: "photo-\nsynthesis" -> "photosynthesis"
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # PDF extraction often merges words together; only fix the obvious cases.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_noise(line: str) -> bool:
    """Whether a line is page furniture rather than content."""
    stripped = line.strip()
    if not stripped:
        return True
    return any(pattern.match(stripped) for pattern in NOISE_RES)


def detect_marks(text: str, default: int = 1) -> int:
    """The mark allocation stated in a question block."""
    matches = list(MARKS_RE.finditer(text or ""))
    if not matches:
        return default
    # Take the largest bracket value in the block; sub-parts inflate counts.
    values = []
    for match in matches:
        value = match.group(1) or match.group(2)
        if value:
            values.append(int(value))
    return max(values) if values else default


def detect_type(text: str, choices: list[str] | None = None) -> str:
    """Infer the question type from a command term."""
    lowered = (text or "").lower()
    for term in _TERM_ORDER:
        if term in lowered:
            return COMMAND_TERMS[term]
    if choices:
        return "multiple_choice"
    return "short_answer"


def extract_choices(block: str) -> tuple[list[str], str]:
    """Split a multiple-choice block into ``(choices, stem)``.

    Returns ``([], block)`` when the block does not look like a multiple-choice
    question at all. A wrapped option line is appended to the option before it.
    """
    lines = block.splitlines()
    matches = [OPTION_RE.match(line) for line in lines]
    found = [(index, match) for index, match in enumerate(matches) if match]

    letters = [match.group(1) for _index, match in found]
    # Require a run of at least three options starting at A.
    if len(letters) < 3 or letters[0] != "A":
        return [], block
    if letters != sorted(letters)[: len(letters)] or len(set(letters)) != len(letters):
        return [], block

    first_option = found[0][0]
    stem = "\n".join(line for line in lines[:first_option] if line.strip()).strip()

    choices: list[str] = []
    for line in lines[first_option:]:
        match = OPTION_RE.match(line)
        if match and (not choices or _next_letter(choices) == match.group(1)):
            choices.append(MARKS_RE.sub("", match.group(2)).strip())
        elif choices and line.strip():
            # A continuation of the previous option (a wrapped line). Drop any
            # trailing mark allocation that the layout put on the same line.
            continuation = MARKS_RE.sub("", line).strip()
            if continuation:
                choices[-1] = f"{choices[-1]} {continuation}".strip()

    if len(choices) < 3:
        return [], block
    return choices, stem


def _next_letter(choices: list[str]) -> str:
    return "ABCDEFGH"[len(choices)]


def answer_from_choices(choices: list[str], answer_letters: str) -> tuple[str, list[str]]:
    """Map a mark-scheme letter (e.g. ``B``) onto its choice text."""
    warnings: list[str] = []
    if not answer_letters:
        return "", warnings

    letter = answer_letters.strip()[0].upper()
    index = "ABCDEFGH".find(letter)
    if index < 0 or index >= len(choices):
        warnings.append(f"mark scheme letter {letter!r} does not match any option")
        return "", warnings
    return choices[index], warnings


# --------------------------------------------------------------------- parser

def parse_questions(
    text: str,
    *,
    topic: str | None = None,
    source: str | None = None,
    answers: dict[int, tuple[str, list[str]]] | None = None,
    min_length: int = 25,
) -> list[ParsedQuestion]:
    """Parse question blocks out of one PDF's text.

    ``answers`` optionally maps question number -> (answer text, warning list),
    which is how a separate mark-scheme document is merged back in.
    """
    answers = answers or {}
    cleaned = clean_text(text)
    blocks = _split_blocks(cleaned)

    questions: list[ParsedQuestion] = []
    for number, block in blocks:
        question = _build_question(
            number, block, topic=topic, source=source, min_length=min_length
        )
        if question is None:
            continue
        if number in answers:
            answer_text, warnings = answers[number]
            question.correct_answer = answer_text
            question.warnings.extend(warnings)
            if question.choices and answer_text in question.choices:
                question.question_type = "multiple_choice"
            elif question.choices:
                # The answer is presumably a letter; try to resolve it.
                resolved, resolve_warnings = answer_from_choices(question.choices, answer_text)
                if resolved:
                    question.correct_answer = resolved
                    question.question_type = "multiple_choice"
                question.warnings.extend(resolve_warnings)
        questions.append(question)

    return questions


def _split_blocks(text: str) -> list[tuple[int, str]]:
    """Split text into ``(question_number, block_text)`` pairs."""
    blocks: list[tuple[int, str]] = []
    current_number: int | None = None
    current_lines: list[str] = []
    seen_numbers: set[int] = set()
    expected = 1

    for raw_line in text.splitlines():
        if is_noise(raw_line):
            continue
        line = raw_line.rstrip()
        match = QUESTION_START_RE.match(line)
        # Only treat it as a new question if the numbering is plausible, so
        # numbers inside a stem ("2.5 cm") are not mistaken for question starts.
        if match and _looks_like_question_start(match, expected, seen_numbers):
            if current_number is not None and current_lines:
                blocks.append((current_number, "\n".join(current_lines).strip()))
            current_number = int(match.group(1))
            seen_numbers.add(current_number)
            expected = current_number + 1
            rest = match.group(2).strip()
            current_lines = [rest] if rest else []
            continue

        if current_number is not None:
            current_lines.append(line)

    if current_number is not None and current_lines:
        blocks.append((current_number, "\n".join(current_lines).strip()))

    return blocks


def _looks_like_question_start(match: re.Match, expected: int, seen: set[int]) -> bool:
    """Guard against decimal numbers and cross-references being read as starts."""
    number = int(match.group(1))
    if number in seen:
        return False
    if number == expected or number == expected + 1:
        return True
    # Allow a gentle jump (some papers skip numbers) but not arbitrary values.
    return expected <= number <= expected + 3


def _build_question(
    number: int,
    block: str,
    *,
    topic: str | None,
    source: str | None,
    min_length: int,
) -> ParsedQuestion | None:
    choices, stem = extract_choices(block)
    text = (stem or block) if choices else block

    # Drop sub-part letters that survived on their own lines.
    lines = [SUBPART_RE.sub(r"\2", line) if SUBPART_RE.match(line) else line for line in text.splitlines()]
    text = "\n".join(lines).strip()

    text = MARKS_RE.sub("", text).strip(" \n-–—:")
    text = re.sub(r"\n{2,}", "\n", text).strip()

    if len(text) < min_length:
        return None

    marks = detect_marks(block)
    question_type = detect_type(text, choices)

    warnings: list[str] = []
    confidence = 0.5

    if marks > 0:
        confidence += 0.2
    if choices and len(choices) >= 3:
        confidence += 0.15
    if 10 <= len(text) <= 1200:
        confidence += 0.1
    else:
        warnings.append("unusual question length; check for merged or split blocks")
        confidence -= 0.1
    if re.search(r"figure|diagram|graph|table|image|shown below", text, re.IGNORECASE):
        warnings.append("question refers to a figure/diagram; attach the image manually")
        confidence -= 0.15
    if question_type == "multiple_choice" and len(choices) < 3:
        warnings.append("marked as multiple choice but fewer than three options were found")
        question_type = "short_answer"

    return ParsedQuestion(
        question_text=text,
        question_type=question_type,
        marks=marks,
        choices=choices,
        topic=topic,
        source=f"{source}#q{number}" if source else f"question {number}",
        confidence=max(0.0, min(1.0, confidence)),
        warnings=warnings,
    )


# ---------------------------------------------------------------- mark scheme

MARK_SCHEME_RE = re.compile(
    r"^\s*(\d{1,2})\s*[.)]?\s*(?:\(([a-h])\)\s*)?(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

#: A mark-scheme line often continues into indented answer lines.
def parse_mark_scheme(text: str) -> dict[int, tuple[str, list[str]]]:
    """Parse a mark-scheme document into ``{question_number: (answer, warnings)}``."""
    cleaned = clean_text(text)
    answers: dict[int, tuple[str, list[str]]] = {}
    current: int | None = None
    buffer: list[str] = []
    expected = 1

    def flush() -> None:
        if current is None or not buffer:
            return
        # Keep the top marking points; a scheme page often lists alternatives.
        points = [
            MARKS_RE.sub("", p).strip(" .-;") for p in buffer if p.strip(" .-;")
        ]
        points = [p for p in points if p]
        text_value = "; ".join(points[:4])
        warnings: list[str] = []
        if len(points) > 4:
            warnings.append("mark scheme had more points than were kept")
        answers[current] = (text_value, warnings)

    for raw_line in cleaned.splitlines():
        if is_noise(raw_line):
            continue
        match = MARK_SCHEME_RE.match(raw_line)
        number = int(match.group(1)) if match else None
        if match and number is not None and (number == expected or number in answers or expected <= number <= expected + 3):
            flush()
            current = number
            expected = number + 1
            buffer = [match.group(3).strip()] if match.group(3).strip() else []
        elif current is not None:
            buffer.append(raw_line.strip())

    flush()
    return answers


def load_text_file(path) -> str:
    """Read an extracted-text file into a single string."""
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8").replace("\f", "\n")
