"""Answer grading.

Four question types are supported and each is marked differently:

* ``multiple_choice`` / ``true_false`` — exact match after normalisation.
* ``calculation`` — numeric comparison with a relative tolerance, unit-aware,
  with common SI prefixes converted so ``25 cm3`` matches ``0.000025 m3``.
* ``short_answer`` — similarity between the response and the model answer,
  with partial credit for covering the mark-scheme keywords.

The module is pure and dependency-free (standard library only) so grading is
identical in the web app, the CLI and the tests.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# --------------------------------------------------------------------- verdict

#: Verdict names, in increasing order of quality.
WRONG = "wrong"
PARTIAL = "partial"
CLOSE = "close"
CORRECT = "correct"


@dataclass
class Verdict:
    """The result of marking one response."""

    correct: bool
    verdict: str
    score: float  # 0-100 similarity/confidence
    marks_awarded: int
    marks_available: int
    feedback: str
    explanation: str = ""
    keywords_matched: list[str] = field(default_factory=list)
    keywords_missed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "correct": self.correct,
            "verdict": self.verdict,
            "score": round(self.score, 1),
            "marks_awarded": self.marks_awarded,
            "marks_available": self.marks_available,
            "feedback": self.feedback,
            "explanation": self.explanation,
            "keywords_matched": self.keywords_matched,
            "keywords_missed": self.keywords_missed,
        }


# ----------------------------------------------------------------- normalising

_PUNCT_RE = re.compile(r"[^\w\s%]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

#: Words ignored when comparing short answers.
STOPWORDS = frozenset(
    """
    a an the and or of to in is are was were be been being it its this that these
    those for with as by from at on into than then so such not no if when while
    which who whom whose what there their they them he she his her you your we our
    can could may might must shall should will would do does did done have has had
    more most less least very also both each other some any all only just about
    between within during including included include using used via towards toward
    across along around because since therefore however whereas whilst while
    """.split()
)

#: Marking points that carry no marks on their own.
_WEAK_TERMS = frozenset(
    "increase decrease higher lower more less large small many few number amount "
    "level rate time value".split()
)

#: Words that are true in almost any answer about the topic. Matching only one of
#: these is never enough for credit, otherwise "a hydrogen bond" would score for
#: "peptide bond".
_GENERIC_TERMS = frozenset(
    """
    bond bonds energy water cell cells molecule molecules reaction process
    structure function form type kind part factor factors affect affects change
    changes thing things way ways made make used use using called known
    """.split()
)

#: Similarity at or above this counts as a confident match without keyword
#: overlap. Set high deliberately: a near-identical phrasing that still gets a
#: key word wrong ("increases then drops" vs "increases then decreases") lands
#: around 90 and must not be awarded full marks.
_CONFIDENT_SIMILARITY = 95.0


#: Terms of at least this length may match inside longer words (inflections).
_STEM_MATCH_MIN_LENGTH = 6


def normalize_text(value: str | None) -> str:
    """Lower-case, strip accents, drop punctuation and collapse whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = text.replace("−", "-")  # unicode minus
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _tokenize(value: str) -> list[str]:
    return [t for t in normalize_text(value).split() if t]


def _content_tokens(value: str) -> list[str]:
    """Tokens with stopwords removed; falls back to all tokens if empty."""
    tokens = [t for t in _tokenize(value) if t not in STOPWORDS]
    return tokens or _tokenize(value)


def similarity(a: str, b: str) -> float:
    """Return a 0-100 similarity between two strings.

    Combines character-level and token-level similarity, which is more robust
    than either alone for short factual answers.
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0

    char_ratio = SequenceMatcher(None, na, nb).ratio() * 100

    tok_a, tok_b = _content_tokens(a), _content_tokens(b)
    if tok_a and tok_b:
        set_a, set_b = set(tok_a), set(tok_b)
        overlap = len(set_a & set_b)
        precision = overlap / len(set_b) if set_b else 0.0
        recall = overlap / len(set_a) if set_a else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        token_ratio = f1 * 100
        if set_a <= set_b or set_b <= set_a:
            # One answer is a subset of the other ("osmosis" vs "osmosis of water").
            token_ratio = max(token_ratio, 85.0)
    else:
        token_ratio = char_ratio

    return max(char_ratio, token_ratio * 0.95)


# --------------------------------------------------------------------- numbers

#: SI prefix multipliers. Only prefixes that appear in biology answers are
#: expanded automatically; "M" (molar) is deliberately excluded because it is
#: both a prefix and a unit.
_PREFIXES: tuple[tuple[str, float], ...] = (
    ("da", 1e1),
    ("y", 1e-24), ("z", 1e-21), ("a", 1e-18), ("f", 1e-15), ("p", 1e-12),
    ("n", 1e-9), ("u", 1e-6), ("µ", 1e-6), ("μ", 1e-6), ("m", 1e-3),
    ("c", 1e-2), ("d", 1e-1), ("h", 1e2), ("k", 1e3),
    ("G", 1e9), ("T", 1e12),
)

#: Units that are also SI prefixes when written alone — never expand these.
_LITERAL_UNITS = frozenset({"m", "g", "s", "l", "a", "k", "c", "d", "h", "n", "p", "f", "t"})

#: Explicit conversions for area, volume and rate units, expressed in the base
#: unit they are compared in. Without these, "cm3" would be read as centi-"m3".
_UNIT_CONVERSIONS: dict[str, tuple[float, str]] = {
    "m2": (1.0, "m2"), "cm2": (1e-4, "m2"), "mm2": (1e-6, "m2"), "dm2": (1e-2, "m2"),
    "m3": (1.0, "m3"), "cm3": (1e-6, "m3"), "mm3": (1e-9, "m3"), "dm3": (1e-3, "m3"),
    "l": (1e-3, "m3"), "ml": (1e-6, "m3"), "ul": (1e-9, "m3"), "µl": (1e-9, "m3"),
    "cl": (1e-5, "m3"), "dl": (1e-4, "m3"),
    "mols-1": (1.0, "mol s-1"), "molmin-1": (1.0 / 60.0, "mol s-1"),
    "m-1": (1.0, "m-1"), "cm-1": (100.0, "m-1"), "mm-1": (1000.0, "m-1"),
    "mins-1": (1.0 / 60.0, "s-1"), "s-1": (1.0, "s-1"), "h-1": (1.0 / 3600.0, "s-1"),
}


@dataclass
class Number:
    """A parsed numeric value with an optional unit."""

    value: float
    unit: str = ""

    def in_base_units(self) -> tuple[float, str, float]:
        """Return ``(value, base_unit, factor)`` with units normalised."""
        unit = self.unit
        if not unit:
            return self.value, "", 1.0

        if unit in _UNIT_CONVERSIONS:
            factor, base = _UNIT_CONVERSIONS[unit]
            return self.value * factor, base, factor

        # "mols-1" style compound units: convert the leading length/volume part.
        for suffix in ("s-1", "min-1"):
            if unit.endswith(suffix):
                head = unit[: -len(suffix)]
                if head in _UNIT_CONVERSIONS:
                    factor, base = _UNIT_CONVERSIONS[head]
                    return self.value * factor, f"{base} {suffix}", factor
                if head.lower() in {"mol", "g", "kg", "mg"}:
                    return self.value, f"{head} {suffix}", 1.0
                break

        if unit in _LITERAL_UNITS:
            return self.value, unit, 1.0

        for prefix, multiplier in _PREFIXES:
            if unit.startswith(prefix) and len(unit) > len(prefix):
                base = unit[len(prefix):]
                if len(base) >= 1:
                    return self.value * multiplier, base, multiplier
        return self.value, unit, 1.0


#: A number, optionally followed by a unit. The mantissa and exponent are parsed
#: together so "1.2e-3" is a single value, and the unit pattern keeps exponent
#: notation such as "s-1" or "dm-3" intact.
_NUMBER_PATTERN = re.compile(
    r"""
    (?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)   # mantissa (+ optional exponent)
    (?:\s*(?P<unit>
        (?![eE][-+]?\d)                                     # not the mantissa exponent
        [a-zA-Zµμ°%][a-zA-Z0-9µμ°%^\-]*(?:\s+[a-zA-Z][a-zA-Z0-9µμ°%^\-]*)*
    ))?
    """,
    re.VERBOSE,
)


def _clean_unit(unit: str | None) -> str:
    """Normalise a unit string for comparison."""
    if not unit:
        return ""
    cleaned = unit.strip().rstrip(".,;")
    cleaned = cleaned.replace("^", "")
    cleaned = cleaned.replace("³", "3").replace("²", "2")
    cleaned = cleaned.replace("·", "")
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    # Preserve the case of molarity units: mM (millimolar) is not mm (millimetre).
    if re.fullmatch(r"[mµμ]?M", cleaned):
        return cleaned
    return cleaned.lower()


def parse_number(text: str | None) -> Number | None:
    """Extract the first numeric value and its unit from ``text``."""
    if not text:
        return None
    cleaned = str(text).replace(",", "")
    for match in _NUMBER_PATTERN.finditer(cleaned):
        try:
            value = float(match.group("num"))
        except (TypeError, ValueError):
            continue
        # A bare exponent marker ("e-3") is not a unit.
        unit = _clean_unit(match.group("unit"))
        if unit in {"e", "e-"} or re.fullmatch(r"[eE][-+]?\d*", unit or ""):
            unit = ""
        return Number(value=value, unit=unit)
    return None


def numbers_match(
    expected: str | None, actual: str | None, tolerance: float = 0.01
) -> tuple[bool, str]:
    """Compare two numeric answers. Returns ``(matches, feedback)``."""
    a = parse_number(expected)
    b = parse_number(actual)
    if a is None or b is None:
        return False, "no numeric value found in the answer"

    a_value, a_unit, _ = a.in_base_units()
    b_value, b_unit, _ = b.in_base_units()

    if a_unit and b_unit and a_unit != b_unit:
        # If the units convert cleanly into one another, the mismatch is a real
        # error (10 mm3 really is not 10 cm3). Otherwise they are simply
        # different quantities and cannot be compared at all.
        _, a_base, _ = a.in_base_units()
        _, b_base, _ = b.in_base_units()
        if a_base == b_base:
            return False, f"unit mismatch: expected {a.unit}, got {b.unit}"
        return False, f"different units: expected {a.unit}, got {b.unit}"

    if a_value == 0:
        return (abs(b_value) < 1e-12, "expected zero")

    relative_error = abs(b_value - a_value) / abs(a_value)
    if relative_error <= tolerance:
        return True, "within tolerance"
    return False, f"off by {relative_error * 100:.1f}%"


# ------------------------------------------------------------------ short answer

def _split_marking_points(model_answer: str) -> list[str]:
    """Split a model answer into marking points on separators."""
    if not model_answer:
        return []
    parts = re.split(r"[;\n]|\s+[-–—]\s+|\s*/\s*|\s*\|\s*", model_answer)
    points = [p.strip(" .;,") for p in parts if p and p.strip(" .;,")]
    return points or [model_answer.strip()]


def _key_terms(point: str) -> list[str]:
    """The terms in a marking point that must appear for credit."""
    terms = [t for t in _content_tokens(point) if t not in _WEAK_TERMS and len(t) > 1]
    return terms


def _stem(token: str) -> str:
    """Crudely reduce a token to a comparable stem.

    Both the mark-scheme term and the student's word go through this function,
    so only self-consistency matters. Regular plurals and the common -ing/-ed
    forms are enough to match "bases" with "base" and "binding" with "binds".
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    return token


def _match_point(
    point: str,
    response: str,
    consumed: set[str] | None = None,
    *,
    threshold: float = _CONFIDENT_SIMILARITY,
) -> tuple[list[str], set[str]]:
    """Match one marking point against a response.

    Returns ``(matched_terms, newly_used_tokens)``. ``consumed`` holds tokens
    already credited to an earlier marking point, so one phrase in the response
    cannot earn marks for two different points.
    """
    consumed = set(consumed or ())
    response_tokens = set(_content_tokens(response)) - consumed

    point_similarity = similarity(point, response)
    confident = point_similarity >= threshold

    matched: list[str] = []
    used: set[str] = set()
    for term in _key_terms(point):
        hit: str | None = None
        if term in response_tokens:
            hit = term
        else:
            # Allow inflections and small typos ("binds" vs "binding",
            # "bases" vs "base").
            term_stem = _stem(term)
            for token in response_tokens:
                if (
                    _stem(token) == term_stem
                    or (
                        len(term) >= _STEM_MATCH_MIN_LENGTH
                        and (term in token or token in term)
                    )
                    or similarity(term, token) >= 85
                ):
                    hit = token
                    break
        if hit is not None:
            matched.append(term)
            used.add(hit)

    terms = _key_terms(point)
    if not terms:
        return matched, used

    # Relevance gate: a partial match built only from generic vocabulary ("bond",
    # "energy", "affect") is not evidence that the point was understood.
    if len(matched) < len(terms) and all(term in _GENERIC_TERMS for term in matched):
        return [], set()

    # Credit the point when the phrasing matches the mark scheme closely...
    if confident:
        return matched, used

    # ...or when every distinctive term of the point is present. Partial
    # keyword coverage is deliberately not credited, even at high string
    # similarity: "increases then drops" must not earn the mark that
    # "increases then decreases" earns, because they mean different things.
    if len(matched) == len(terms) and any(term not in _GENERIC_TERMS for term in terms):
        return matched, used

    return [], set()


def _split_marking_points_texts(model_answer: str) -> list[str]:  # pragma: no cover - helper
    return _split_marking_points(model_answer)


def _covered_points(model_answer: str, response: str) -> list[str]:
    """The marking points a response covers, without double-counting tokens."""
    covered: list[str] = []
    consumed: set[str] = set()
    for point in _split_marking_points(model_answer):
        matched, used = _match_point(point, response, consumed)
        if matched:
            covered.append(point)
            consumed |= used
    return covered


def _point_covered(point: str, response: str, threshold: float = _CONFIDENT_SIMILARITY) -> bool:
    """Whether a single marking point is present in a response (no context)."""
    if not response.strip():
        return False
    matched, _used = _match_point(point, response, threshold=threshold)
    return bool(matched)


# ------------------------------------------------------------------- entry point

def grade(
    question,
    response: str | None,
    *,
    fuzzy_threshold: float = 72.0,
    tolerance: float = 0.01,
) -> Verdict:
    """Mark ``response`` against ``question``.

    ``question`` is anything exposing ``question_type``, ``correct_answer``,
    ``marks`` and ``explanation`` (a :class:`~app.models.Question` works).
    Empty responses are always wrong and never count as a "close" guess.
    """
    qtype = (getattr(question, "question_type", "") or "short_answer").strip()
    expected = (getattr(question, "correct_answer", "") or "").strip()
    marks = int(getattr(question, "marks", 1) or 1)
    explanation = getattr(question, "explanation", "") or ""
    choices = list(getattr(question, "choices", []) or [])
    answer = (response or "").strip()

    if not answer:
        return Verdict(
            correct=False,
            verdict=WRONG,
            score=0.0,
            marks_awarded=0,
            marks_available=marks,
            feedback="No answer submitted.",
            explanation=explanation,
        )

    # ---------------------------------------------------------- exact types
    if qtype in {"multiple_choice", "true_false"}:
        # Accept a letter ("B") or an index ("1") as well as the option text.
        matched = normalize_text(answer) == normalize_text(expected)
        if not matched and choices:
            letter_index = _choice_index_from_letter(answer, choices)
            if letter_index is not None and normalize_text(choices[letter_index]) == normalize_text(expected):
                matched = True
        if matched:
            return Verdict(
                correct=True,
                verdict=CORRECT,
                score=100.0,
                marks_awarded=marks,
                marks_available=marks,
                feedback="Correct.",
                explanation=explanation,
            )
        return Verdict(
            correct=False,
            verdict=WRONG,
            score=0.0,
            marks_awarded=0,
            marks_available=marks,
            feedback="Incorrect.",
            explanation=explanation,
        )

    # ------------------------------------------------------------ calculation
    if qtype == "calculation":
        ok, why = numbers_match(expected, answer, tolerance=tolerance)
        if ok:
            return Verdict(
                correct=True,
                verdict=CORRECT,
                score=100.0,
                marks_awarded=marks,
                marks_available=marks,
                feedback="Correct.",
                explanation=explanation,
            )
        # Almost right: right number, wrong unit, is worth partial credit.
        expected_num = parse_number(expected)
        given_num = parse_number(answer)
        if expected_num and given_num and _values_close(expected_num, given_num, tolerance):
            if not given_num.unit:
                # A numeric answer without units cannot be credited at all.
                return Verdict(
                    correct=False,
                    verdict=WRONG,
                    score=40.0,
                    marks_awarded=0,
                    marks_available=marks,
                    feedback=f"Right number, but the unit is missing — include {expected_num.unit}.",
                    explanation=explanation,
                )
            return Verdict(
                correct=False,
                verdict=PARTIAL,
                score=70.0,
                marks_awarded=max(1, marks - 1),
                marks_available=marks,
                feedback=f"Nearly — check the unit ({why}).",
                explanation=explanation,
            )
        return Verdict(
            correct=False,
            verdict=WRONG,
            score=0.0,
            marks_awarded=0,
            marks_available=marks,
            feedback=f"Incorrect ({why}).",
            explanation=explanation,
        )

    # ----------------------------------------------------------- short answer
    points = _split_marking_points(expected)
    matched_points = [p for p in _covered_points(expected, answer)]
    missed_points = [p for p in points if p not in matched_points]

    if points:
        coverage = len(matched_points) / len(points)
    else:  # pragma: no cover - a short_answer without a model answer
        coverage = 0.0

    direct_similarity = similarity(expected, answer)

    marks_awarded = 0
    if coverage >= 1.0 or direct_similarity >= 97:
        marks_awarded = marks
    elif points:
        # Partial credit scales with how many marking points were covered.
        marks_awarded = min(marks - 1, max(0, round(coverage * marks))) if marks > 1 else 0
        if coverage >= 0.5 and marks == 1:
            marks_awarded = 0  # a 1-mark question needs the full point

    correct = marks_awarded == marks

    if correct:
        verdict, feedback = CORRECT, "Correct."
    elif marks_awarded > 0:
        verdict = PARTIAL
        feedback = (
            f"Partly correct — {marks_awarded}/{marks} marks. "
            f"You covered {len(matched_points)} of {len(points)} required point"
            f"{'s' if len(points) != 1 else ''}."
        )
    elif direct_similarity >= fuzzy_threshold:
        verdict = CLOSE
        feedback = "Close, but the key marking points were not stated precisely enough."
    else:
        verdict = WRONG
        feedback = "Incorrect."

    return Verdict(
        correct=correct,
        verdict=verdict,
        score=direct_similarity,
        marks_awarded=marks_awarded,
        marks_available=marks,
        feedback=feedback,
        explanation=explanation,
        keywords_matched=matched_points,
        keywords_missed=missed_points,
    )


# --------------------------------------------------------------------- helpers

def _values_close(a: Number, b: Number, tolerance: float) -> bool:
    a_value, _, _ = a.in_base_units()
    b_value, _, _ = b.in_base_units()
    if a_value == 0:
        return abs(b_value) < 1e-12
    return abs(b_value - a_value) / abs(a_value) <= max(tolerance, 0.05)


def _choice_index_from_letter(answer: str, choices: list[str]) -> int | None:
    """Map "B", "b)" or "2" onto a choice index."""
    text = answer.strip()
    if len(text) == 1 and text.isalpha():
        index = ord(text.lower()) - ord("a")
    elif text.isdigit():
        index = int(text) - 1
    else:
        match = re.fullmatch(r"\(?([a-dA-D])\)?[.)]?", text)
        if not match:
            return None
        index = ord(match.group(1).lower()) - ord("a")
    return index if 0 <= index < len(choices) else None
