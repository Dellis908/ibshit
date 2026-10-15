"""Convert rdojo question-bank JSON into this app's import format.

The upstream format (``public/questionbank/<subject>/<theme>/<topic>/questionbank.json``)
is richer than the app's, so the conversion is explicit and testable:

===================  =========================================================
Upstream             Here
===================  =========================================================
``questionType: MC`` → one ``multiple_choice`` question; the option marked
                     ``correct`` becomes the answer, the chosen option's
                     ``markscheme`` becomes the explanation.
``questionType: LA`` → one question **per part**; the part's ``content`` is the
                     stem and its ``markscheme`` becomes the model answer,
                     preserving one marking point per line.
``level``            → ``hl``/``sl``/``both`` kept as a tag.
``difficulty`` (1-8)→ ``easy`` / ``medium`` / ``hard``.
LaTeX, Markdown and  → HTML the templates can render.
``<marks>…</marks>`` tags
===================  =========================================================

Everything here is pure: no network and no database, so it is unit testable.
"""

from __future__ import annotations

import html as html_lib
import re
from collections import Counter
from dataclasses import dataclass, field

#: Difficulty (1-8 upstream) mapped onto the app's three bands.
DIFFICULTY_BANDS = ((1, 3, "easy"), (4, 6, "medium"), (7, 8, "hard"))

#: Upstream subject slug -> the app's topic catalogue prefix.
SUBJECT_PREFIX = "ib-biology-new"

#: Words that introduce a mark allocation inside a mark scheme.
_MARK_TAG_RE = re.compile(r"<marks?>(.*?)</marks?>", re.IGNORECASE | re.DOTALL)
_MARK_WORD_RE = re.compile(r"^\s*\(?\d+\s*marks?\)?\s*$", re.IGNORECASE)

#: Inline math and bold/italic markdown.
_MATH_INLINE_RE = re.compile(r"\$([^$\n]+)\$")
_MATH_DISPLAY_RE = re.compile(r"\$\$([^$]+)\$\$")
_IMG_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)\)")
_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<url>[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(?P<text>[^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?P<text>[^*\n]+)\*(?![\w*])")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)

#: A Markdown table separator row, e.g. ``| :--- | ---: |``.
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
#: Sub/superscript helpers: convert ``_x`` / ``^x`` fragments from LaTeX.
_SUB_RE = re.compile(r"_\{([^}]*)\}|_([A-Za-z0-9])")
_SUP_RE = re.compile(r"\^\{([^}]*)\}|\^([A-Za-z0-9+\-])")


#: Hosts whose images the app is willing to display.
ALLOWED_IMAGE_HOSTS = ("pub-images.revisiondojo.com",)

#: Matches an <img src="..."> tag.
_IMG_TAG_RE = re.compile(r'<img[^>]*src="([^"]+)"[^>]*>', re.IGNORECASE)


def comparison_key(fragment: str) -> str:
    """A text key for a choice, with image URLs kept so images stay distinct.

    Two image-based options are different answers even though they contain no
    words, so stripping tags entirely would make them look like duplicates.
    """
    if not fragment:
        return ""
    with_images = _IMG_TAG_RE.sub(lambda m: f" [image:{m.group(1)}] ", fragment)
    text = re.sub(r"<[^>]+>", " ", with_images)
    return re.sub(r"\s+", " ", text).strip().lower()


# --------------------------------------------------------------------- helpers

@dataclass
class Converted:
    """Result of converting one bank file."""

    questions: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


def difficulty_band(value) -> str:
    """Map an upstream 1-8 difficulty onto easy/medium/hard."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "medium"
    for low, high, band in DIFFICULTY_BANDS:
        if low <= number <= high:
            return band
    return "medium"


def latex_to_html(text: str) -> str:
    """Render the small LaTeX subset used in the bank as readable HTML.

    Only what actually appears upstream is handled: subscripts, superscripts,
    common symbols and simple fractions. Unknown commands fall back to their
    bare name rather than leaving ``\\alpha`` in front of a student.
    """
    if not text:
        return ""

    out = _MATH_DISPLAY_RE.sub(lambda m: latex_to_html(m.group(1)), text)
    out = _MATH_INLINE_RE.sub(lambda m: latex_to_html(m.group(1)), out)

    # Math environments are prose-hostile: unwrap them.
    out = re.sub(
        r"\\begin\{(?:aligned|align|array|gathered|cases)\}\*?\}(.*?)\\end\{(?:aligned|align|array|gathered|cases)\}\*?\}",
        lambda m: m.group(1),
        out,
        flags=re.DOTALL,
    )
    out = re.sub(r"\\begin\{[a-z*]+\}|\\end\{[a-z*]+\}", " ", out)
    # Alignment markers and line breaks inside math.
    out = re.sub(r"\\\\", " ", out)
    out = out.replace("&", " ")

    # Fractions: \frac{a}{b} -> a/b
    out = re.sub(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", out)

    # Text wrappers: \text{...} / \mathrm{...}
    out = re.sub(r"\\(?:text|mathrm|mathbf|mathit)\s*\{([^{}]*)\}", r"\1", out)

    # Symbols are mapped to real Unicode rather than HTML entities, because a
    # later "strip unknown commands" pass would otherwise eat the leading "&".
    symbols = {
        r"\rightarrow": "\u2192", r"\to": "\u2192", r"\leftarrow": "\u2190",
        r"\uparrow": "\u2191", r"\downarrow": "\u2193",
        r"\rightleftharpoons": "\u21cc", r"\leftrightharpoons": "\u21cc",
        r"\times": "\u00d7", r"\div": "\u00f7", r"\pm": "\u00b1",
        r"\approx": "\u2248", r"\leq": "\u2264", r"\geq": "\u2265",
        r"\neq": "\u2260", r"\propto": "\u221d", r"\infty": "\u221e",
        r"\partial": "\u2202", r"\cdot": "\u00b7", r"\degree": "\u00b0",
        r"\alpha": "\u03b1", r"\beta": "\u03b2", r"\gamma": "\u03b3",
        r"\delta": "\u03b4", r"\Delta": "\u0394", r"\epsilon": "\u03b5",
        r"\theta": "\u03b8", r"\kappa": "\u03ba", r"\lambda": "\u03bb",
        r"\mu": "\u03bc", r"\nu": "\u03bd", r"\pi": "\u03c0",
        r"\rho": "\u03c1", r"\sigma": "\u03c3", r"\tau": "\u03c4",
        r"\phi": "\u03c6", r"\omega": "\u03c9", r"\Omega": "\u03a9",
        r"\%": "%", r"\&": "&", r"\_": "_", r"\#": "#", r"\$": "$",
        r"\,": " ", r"\;": " ", r"\:": " ", r"\!": "",
    }
    for command, replacement in symbols.items():
        out = out.replace(command, replacement)

    # Sub/superscripts, then any leftover spacing commands.
    out = _SUB_RE.sub(lambda m: f"<sub>{m.group(1) or m.group(2)}</sub>", out)
    out = _SUP_RE.sub(lambda m: f"<sup>{m.group(1) or m.group(2)}</sup>", out)
    out = re.sub(r"\\[,;:! ]", " ", out)
    out = out.replace(r"\\", "<br>")
    # Any remaining unknown command loses its backslash, and stray math
    # delimiters are dropped so no "$" ever reaches the student.
    out = re.sub(r"\\([A-Za-z]+)", r"\1", out)
    out = out.replace("{", "").replace("}", "").replace("$", "")
    return out


def inline_html(text: str) -> str:
    """Convert short inline content (an option, a stem fragment) to HTML.

    Paragraph wrappers are removed because these fragments are placed inside
    their own markup by the templates; leaving ``<p>`` around a single word also
    makes the stored answer harder to compare and read.
    """
    rendered = markdown_to_html(latex_to_html(text or ""))
    rendered = re.sub(r"</p>\s*<p>", " ", rendered)
    return re.sub(r"^<p>|</p>$", "", rendered.strip()).strip()


def markdown_to_html(text: str) -> str:
    """Convert the Markdown subset used upstream into HTML."""
    if not text:
        return ""

    out = text.replace("\r\n", "\n")

    # Images first (so the link rule does not eat them). Keep the URL: the app
    # mirrors images locally, but a working URL is the safe fallback.
    out = _IMG_RE.sub(
        lambda m: f'<img src="{html_lib.escape(m.group("url"), quote=True)}" '
        f'alt="{html_lib.escape(m.group("alt") or "diagram", quote=True)}">',
        out,
    )
    out = _LINK_RE.sub(
        lambda m: f'<a href="{html_lib.escape(m.group("url"), quote=True)}">'
        f"{m.group('text')}</a>",
        out,
    )

    out = _convert_tables(out)
    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group('text')}</strong>", out)
    out = _ITALIC_RE.sub(lambda m: f"<em>{m.group('text')}</em>", out)
    out = _HR_RE.sub("", out)
    out = out.replace("_\u200b", "")

    # Remaining line breaks become paragraphs / <br>.
    lines = [line.strip() for line in out.split("\n")]
    blocks: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if not line:
            if buffer:
                blocks.append(" ".join(buffer))
                buffer = []
            continue
        if line.startswith("<table") or line.endswith("</table>"):
            if buffer:
                blocks.append(" ".join(buffer))
                buffer = []
            blocks.append(line)
            continue
        buffer.append(line)
    if buffer:
        blocks.append(" ".join(buffer))

    rendered = []
    for block in blocks:
        if block.startswith("<table") or block.startswith("<img"):
            rendered.append(block)
        else:
            rendered.append(f"<p>{block}</p>")
    return "".join(rendered)


def _convert_tables(text: str) -> str:
    """Turn GitHub-style Markdown tables into HTML tables."""
    lines = text.split("\n")
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        is_header = line.strip().startswith("|") and line.strip().endswith("|")
        if (
            is_header
            and index + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[index + 1])
        ):
            header_cells = _split_row(line)
            index += 2
            body_rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                body_rows.append(_split_row(lines[index]))
                index += 1

            table = ["<table><thead><tr>"]
            table += [f"<th>{cell}</th>" for cell in header_cells]
            table.append("</tr></thead><tbody>")
            for row in body_rows:
                table.append("<tr>")
                table += [f"<td>{cell}</td>" for cell in row]
                table.append("</tr>")
            table.append("</tbody></table>")
            output.append("".join(table))
            continue

        output.append(line)
        index += 1

    return "\n".join(output)


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def clean_mark_scheme(raw: str, marks_hint: int | None = None) -> tuple[str, int]:
    """Turn an upstream mark scheme into a model answer plus a mark count.

    Returns ``(model_answer, marks)``. Each upstream bullet becomes one
    semicolon-separated marking point, which is the separator the grading engine
    already understands, so partial credit works without further changes.
    """
    if not raw:
        return "", max(1, marks_hint or 1)

    text = raw.replace("\r\n", "\n")
    tag_marks = 0
    for match in _MARK_TAG_RE.finditer(text):
        inner = match.group(1)
        numbers = re.findall(r"\d+", inner)
        tag_marks += int(numbers[0]) if numbers else 1
    text = _MARK_TAG_RE.sub(" ", text)

    # Split into candidate points on bullets and blank lines.
    candidates: list[str] = []
    for chunk in re.split(
        r"\n\s*\n|\n(?=\s*[-*\u2022])|^\s*[-*\u2022]\s*", text, flags=re.MULTILINE
    ):
        for piece in chunk.split("\n"):
            piece = piece.strip()
            if piece:
                candidates.append(piece)

    points: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"^\s*[-*\u2022]+\s*", "", candidate).strip()
        if not candidate or _MARK_WORD_RE.match(candidate):
            continue
        # "_OR_" separates acceptable alternatives for the same mark, so they
        # stay inside one point.
        candidate = re.sub(r"_{1,2}OR_{1,2}", " or ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+", " ", candidate)
        candidate = markdown_to_html(latex_to_html(candidate))
        candidate = re.sub(r"</?p>", "", candidate)
        if candidate and candidate not in points:
            points.append(candidate)

    model = "; ".join(points)
    total = tag_marks or marks_hint or len(points)
    return model, max(1, total)


def _explanation_text(value) -> str:
    """Flatten an upstream explanation into readable HTML.

    The field is either absent, a string, or a worked-solution tree
    (``{"methods": [{"steps": [{"title", "content"}]}]}``). For a tree we keep
    the step content, preferring any step that states the final answer, and cap
    the length so a single explanation cannot bloat the question bank.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return markdown_to_html(latex_to_html(value)).replace("<p>", "").replace("</p>", "")

    if isinstance(value, dict):
        steps: list[dict] = []
        for method in value.get("methods") or []:
            for step in method.get("steps") or []:
                if step.get("type") in (None, "text") and step.get("content"):
                    steps.append(step)
        if not steps:
            return ""

        # The final-answer step is included with the rest, so no selection is
        # needed: every step's prose is useful as an explanation.
        pieces: list[str] = []
        for step in steps:
            content = latex_to_html(step.get("content") or "")
            content = markdown_to_html(content).replace("<p>", " ").replace("</p>", " ")
            content = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()
            if content:
                pieces.append(content)

        text = " ".join(pieces)
        # Drop the "Final Answer:" scaffolding if the content repeats the answer.
        text = re.sub(r"\bFinal Answer:\s*", "", text, flags=re.IGNORECASE)
        return text[:1200].strip()

    return ""


def plain_text_length(text: str) -> int:
    """Roughly how much readable text a fragment has (tags stripped)."""
    return len(re.sub(r"<[^>]+>", "", text or "").strip())


def _level_tags(level: str | None) -> list[str]:
    value = (level or "").lower()
    if value in {"hl", "sl", "both"}:
        return [value] if value != "both" else ["hl", "sl"]
    return []


def _sub_topic_labels(question: dict) -> list[str]:
    """The curriculum codes attached to a question (e.g. ``A1.1.4``)."""
    codes: list[str] = []
    metadata = question.get("metadata") or {}
    for label in metadata.get("labeledSubSubTopics") or []:
        title = (label.get("title") or "").strip()
        code = title.split(" ", 1)[0] if title else ""
        if code and code not in codes:
            codes.append(code)
    return codes


# ------------------------------------------------------------------ conversion

def convert_question(
    question: dict,
    *,
    topic_slug: str,
    subject_slug: str = SUBJECT_PREFIX,
    image_base: str | None = None,
) -> Converted:
    """Convert one upstream question into zero or more app questions."""
    result = Converted()

    qid = question.get("id")
    level = question.get("level")
    paper = question.get("paper")
    difficulty = difficulty_band(question.get("difficulty"))
    stem = markdown_to_html(latex_to_html(question.get("specification") or ""))
    codes = _sub_topic_labels(question)
    question_set = question.get("questionSet")

    tags = ["rdojo", subject_slug] + _level_tags(level)
    if question_set:
        tags.append(str(question_set).lower())
    tags.extend(code.lower().replace(".", "-") for code in codes)

    images = re.findall(r'<img src="([^"]+)"', stem)
    result.images.extend(images)

    options = question.get("options") or []
    parts = question.get("parts") or []

    # ---------------------------------------------------------- multiple choice
    if options:
        correct = [o for o in options if o.get("correct")]
        if len(correct) != 1 or len(options) < 2:
            result.skipped.append(f"{qid}: {len(correct)} correct options out of {len(options)}")
            return result
        if not stem.strip():
            # A question with no stem is unanswerable: usually a stray part.
            result.skipped.append(f"{qid}: multiple choice with no question text")
            return result

        answer = inline_html((correct[0].get("content") or "").strip())
        choices = [
            inline_html((option.get("content") or "").strip())
            for option in sorted(options, key=lambda o: o.get("order", 0))
        ]
        if answer not in choices:
            result.skipped.append(f"{qid}: correct option missing from choices")
            return result

        keys = [comparison_key(choice) for choice in choices]
        if len(set(keys)) != len(keys):
            duplicates = [k for k, n in Counter(keys).items() if n > 1]
            result.skipped.append(
                f"{qid}: duplicate options after conversion ({len(duplicates)})"
            )
            return result

        rationale = (correct[0].get("markscheme") or "").strip()
        explanation = markdown_to_html(latex_to_html(rationale)) if rationale else ""
        explanation = re.sub(r"</?p>", "", explanation).strip()
        # Upstream rationale often opens with "Correct: " — drop the prefix.
        explanation = re.sub(r"^(Correct|Correct answer)\s*:\s*", "", explanation, flags=re.IGNORECASE)

        result.questions.append(
            {
                "topic": topic_slug,
                "type": "multiple_choice",
                "question_text": stem,
                "correct_answer": answer,
                "choices": choices,
                "marks": 1,
                "explanation": explanation,
                "tags": tags,
                "difficulty": difficulty,
                "source": f"rdojo:{qid}" + (f" ({paper})" if paper else ""),
            }
        )
        return result

    # ------------------------------------------------------- long answer parts
    if parts:
        emitted = 0
        for order, part in enumerate(sorted(parts, key=lambda p: p.get("order", 0))):
            part_content = markdown_to_html(
                latex_to_html((part.get("content") or "").strip())
            )
            answer, marks = clean_mark_scheme(part.get("markscheme") or "")
            if not answer.strip():
                result.skipped.append(f"{qid}#{order}: no mark scheme")
                continue
            if plain_text_length(part_content) < 8:
                result.skipped.append(f"{qid}#{order}: empty question text")
                continue

            part_images = re.findall(r'<img src="([^"]+)"', part_content)
            result.images.extend(part_images)

            # Longer answers get the stem as context so the question stands alone.
            text = part_content
            if stem.strip() and emitted == 0 and plain_text_length(stem) > 20:
                text = f"{stem}{part_content}"

            result.questions.append(
                {
                    "topic": topic_slug,
                    "type": "short_answer",
                    "question_text": text,
                    "correct_answer": answer,
                    "choices": [],
                    "marks": marks,
                    "explanation": _explanation_text(part.get("explanation")),
                    "tags": tags + ["long-answer"],
                    "difficulty": difficulty,
                    "source": f"rdojo:{qid}#{order}" + (f" ({paper})" if paper else ""),
                }
            )
            emitted += 1

        if not emitted:
            result.skipped.append(f"{qid}: long answer produced no usable parts")
        return result

    # ------------------------------------------------------- unusable question
    result.skipped.append(f"{qid}: no options and no parts")
    return result


def convert_bank(
    payload: dict,
    *,
    topic_slug: str,
    subject_slug: str = SUBJECT_PREFIX,
) -> Converted:
    """Convert a whole ``questionbank.json`` payload."""
    envelope = payload.get("json") if isinstance(payload.get("json"), dict) else payload
    questions = (envelope or {}).get("questions") or []

    combined = Converted()
    for question in questions:
        converted = convert_question(question, topic_slug=topic_slug, subject_slug=subject_slug)
        combined.questions.extend(converted.questions)
        combined.skipped.extend(converted.skipped)
        combined.images.extend(converted.images)
    return combined


def topic_slug_from_path(path: str) -> str:
    """``ib-biology-new/.../ib-biology-new-a11-water`` -> ``bio-a11-water``."""
    leaf = path.rstrip("/").split("/")[-1]
    leaf = leaf.replace(f"{SUBJECT_PREFIX}-", "")
    return f"bio-{leaf}"
