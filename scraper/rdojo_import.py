"""Import the rdojo question bank into this app's format.

    python -m scraper.rdojo_import list                  # what is available
    python -m scraper.rdojo_import fetch                 # download + convert all
    python -m scraper.rdojo_import fetch --topic bio-a11-water
    python -m scraper.rdojo_import fetch --limit 3       # a small trial run

The source serves the bank as Git LFS objects behind a public Forgejo instance,
so the pipeline is: read the index -> resolve each ``questionbank.json`` LFS
pointer -> download the object -> convert (``scraper/rdojo.py``) -> write an
importable JSON file plus a review file for anything skipped.

Everything is cached under ``data/rdojo/`` and re-running is cheap.

Only material you are entitled to use should be imported. This tool reads a
public repository exactly as a browser would; check the source's terms before
redistributing anything it produces.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import rdojo
from scraper.config import BASE_DIR

# ------------------------------------------------------------------ constants

FORGE_HOST = "https://git.pirateib.sh"
REPO = "pirateIB/rdojo"
API = f"{FORGE_HOST}/api/v1/repos/{REPO}"
LFS_BATCH = f"{FORGE_HOST}/{REPO}.git/info/lfs/objects/batch"
LFS_OBJECT = f"{FORGE_HOST}/{REPO}.git/info/lfs/objects"

#: A browser User-Agent; the API rejects the default urllib agent with HTTP 403.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

CACHE_DIR = BASE_DIR / "data" / "rdojo"
BANKS_DIR = CACHE_DIR / "banks"
OUTPUT_FILE = BASE_DIR / "seed" / "rdojo.json"
REVIEW_FILE = CACHE_DIR / "review.md"
INDEX_CACHE = CACHE_DIR / "index.json"

DEFAULT_SUBJECT = "ib-biology-new"


class RdojoError(RuntimeError):
    """Raised when the source cannot be read."""


# --------------------------------------------------------------------- fetching

def _request(url: str, *, data: bytes | None = None, accept: str | None = None, timeout: int = 60):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RdojoError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RdojoError(f"could not reach {url}: {exc.reason}") from exc


def fetch_index(*, refresh: bool = False, subject: str = DEFAULT_SUBJECT) -> list[dict]:
    """Return the topic index entries for one subject."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if INDEX_CACHE.exists() and not refresh:
        entries = json.loads(INDEX_CACHE.read_text())
    else:
        # The index itself is an LFS object, but Forgejo also exposes the
        # rendered JSON through the contents API, which is one round trip.
        raw = _request(
            f"{API}/raw/public/questionbank-index.json?ref=master",
            accept="application/vnd.git-lfs+json",
        ).decode("utf-8", "replace")

        oid = _pointer_oid(raw)
        if oid:
            payload = _request_object(oid)
        else:
            payload = raw.encode()
        entries = json.loads(payload.decode("utf-8"))
        INDEX_CACHE.write_text(json.dumps(entries))

    return [e for e in entries["entries"] if e["subjectSlug"] == subject]


def _pointer_oid(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            return line.split(":", 1)[1].strip()
    return None


def _request_object(oid: str, *, timeout: int = 120) -> bytes:
    """Download one Git LFS object by its OID."""
    return _request(f"{LFS_OBJECT}/{oid}", timeout=timeout)


def resolve_oid(path: str, *, refresh: bool = False) -> str | None:
    """Resolve ``<path>/questionbank.json`` to its LFS object id."""
    raw = _request(
        f"{API}/raw/public/questionbank/{path}/questionbank.json?ref=master"
    ).decode("utf-8", "replace")
    return _pointer_oid(raw)


@dataclass
class FetchReport:
    """What a fetch run produced."""

    topics: int = 0
    questions: int = 0
    skipped: int = 0
    duplicates: int = 0
    images: int = 0
    failed: list[str] = field(default_factory=list)
    by_type: Counter = field(default_factory=Counter)
    by_topic: Counter = field(default_factory=Counter)
    skipped_reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"topics fetched:  {self.topics}",
            f"questions:       {self.questions}",
            f"skipped:         {self.skipped}",
            f"cross-topic dupes:{self.duplicates:>6}",
            f"image refs:      {self.images}",
        ]
        if self.by_type:
            lines.append("by type:")
            for key, value in sorted(self.by_type.items()):
                lines.append(f"  {key}: {value}")
        if self.failed:
            lines.append(f"failed topics ({len(self.failed)}):")
            lines.extend(f"  - {name}" for name in self.failed[:10])
        if self.skipped_reasons:
            lines.append("first skip reasons:")
            lines.extend(f"  - {reason}" for reason in self.skipped_reasons[:10])
        return "\n".join(lines)


def fetch_banks(
    *,
    subject: str = DEFAULT_SUBJECT,
    only_topics: list[str] | None = None,
    limit: int | None = None,
    refresh: bool = False,
    delay: float = 0.3,
) -> tuple[list[dict], FetchReport]:
    """Download and convert every bank for a subject."""
    BANKS_DIR.mkdir(parents=True, exist_ok=True)
    entries = fetch_index(refresh=refresh, subject=subject)
    if not entries:
        raise RdojoError(f"no topics found for subject {subject!r}")

    if only_topics:
        wanted = set(only_topics)
        entries = [e for e in entries if rdojo.topic_slug_from_path(e["path"]) in wanted]
    if limit:
        entries = entries[:limit]

    report = FetchReport()
    questions: list[dict] = []

    for index, entry in enumerate(entries, start=1):
        slug = rdojo.topic_slug_from_path(entry["path"])
        cache_file = BANKS_DIR / f"{slug}.json"
        label = f"[{index}/{len(entries)}]"

        try:
            if cache_file.exists() and not refresh:
                payload = json.loads(cache_file.read_text())
                state = "cached"
            else:
                oid = resolve_oid(entry["path"], refresh=refresh)
                if not oid:
                    raise RdojoError("no LFS pointer found")
                payload = json.loads(_request_object(oid).decode("utf-8"))
                cache_file.write_text(json.dumps(payload))
                state = "fetched"
        except Exception as exc:
            print(f"{label} FAILED  {slug} — {exc}")
            report.failed.append(f"{slug}: {exc}")
            continue

        converted = rdojo.convert_bank(payload, topic_slug=slug)
        questions.extend(converted.questions)
        report.topics += 1
        report.questions += len(converted.questions)
        report.skipped += len(converted.skipped)
        report.images += len(converted.images)
        report.by_type.update(q["type"] for q in converted.questions)
        report.by_topic[slug] = len(converted.questions)
        report.skipped_reasons.extend(converted.skipped[:3])

        print(
            f"{label} {state:8} {slug:42} {len(converted.questions):>4} questions"
            + (f"  ({len(converted.skipped)} skipped)" if converted.skipped else "")
        )
        time.sleep(delay)

    return questions, report


def deduplicate(questions: list[dict]) -> tuple[list[dict], int]:
    """Drop questions that the source lists under more than one topic.

    Revision Dojo re-uses the same question across related subtopics (identical
    source id and text). Keeping every copy would show the same question twice
    in a mixed quiz and double-count it in progress statistics, so the first
    occurrence in syllabus order wins.
    """
    seen: dict[str, dict] = {}
    ordered: list[dict] = []
    removed = 0

    for question in questions:
        # The source id keeps the part index (``<uuid>#2``) so the separate
        # parts of one long-answer question stay distinct.
        key = (question.get("source") or "").strip()
        if not key:
            key = " ".join(
                f"{question.get('question_text') or ''}|{question.get('correct_answer') or ''}".split()
            ).lower()

        if key in seen:
            removed += 1
            continue
        seen[key] = question
        ordered.append(question)

    return ordered, removed


def write_outputs(
    questions: list[dict],
    report: FetchReport,
    *,
    output: Path = OUTPUT_FILE,
    review: Path = REVIEW_FILE,
) -> tuple[Path, Path | None]:
    """Write the importable JSON file and a review file for skipped items."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "meta": {
                    "source": f"{FORGE_HOST}/{REPO}",
                    "subject": DEFAULT_SUBJECT,
                    "syllabus": "IB Biology (first assessment 2025)",
                    "version": 1,
                    "count": len(questions),
                    "converter": "scraper/rdojo.py",
                },
                "questions": questions,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    review_path: Path | None = None
    if report.skipped_reasons or report.failed:
        review.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# rdojo import — items needing review", ""]
        if report.failed:
            lines.append("## Topics that failed to download")
            lines.extend(f"- {name}" for name in report.failed)
            lines.append("")
        if report.skipped_reasons:
            lines.append("## Questions skipped during conversion")
            lines.extend(f"- {reason}" for reason in report.skipped_reasons)
            lines.append("")
        review.write_text("\n".join(lines), encoding="utf-8")
        review_path = review

    return output, review_path


# ------------------------------------------------------------- image integrity

#: A question that points at a visual it does not actually contain cannot be
#: answered, so those questions are dropped rather than shipped broken.
#:
#: "below"/"above" on their own are deliberately excluded: they appear in plenty
#: of self-contained questions and caused false positives.
_FIGURE_WORDS = re.compile(
    r"\b(diagram|figure|graph|chart|image|photograph|micrograph|electrophoresis gel|"
    r"punnett square|gel)\b",
    re.IGNORECASE,
)
_TABLE_WORDS = re.compile(r"\btable\b", re.IGNORECASE)
_VISUAL_TAG_RE = re.compile(r"<(?:img|table)\b", re.IGNORECASE)
_TABLE_TAG_RE = re.compile(r"<table\b", re.IGNORECASE)


def needs_missing_visual(text: str) -> str | None:
    """Why a question is unanswerable, or ``None`` when it stands on its own.

    Content is only ever missing when the question *refers* to a figure or table
    that is not present in its own markup.
    """
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    if _FIGURE_WORDS.search(plain) and not _VISUAL_TAG_RE.search(text):
        return "refers to a figure that is not present"
    if _TABLE_WORDS.search(plain) and not _TABLE_TAG_RE.search(text):
        return "refers to a table that is not present"
    return None

_IMG_SRC_RE = re.compile(r'<img[^>]*src="([^"]+)"[^>]*>', re.IGNORECASE)


def check_image_urls(urls: list[str], *, workers: int = 16) -> dict[str, bool]:
    """Return ``{url: reachable}`` for every distinct URL.

    The upstream image CDN is not permanent: most of the URLs referenced by the
    question bank now return 404. Probing first means the imported bank never
    contains a question that silently depends on a missing diagram.
    """
    import urllib.error
    import urllib.request
    import concurrent.futures as futures

    from app.images import candidates

    def probe(url: str) -> tuple[str, bool]:
        """True if any known mirror still serves this image."""
        for candidate in candidates(url):
            request = urllib.request.Request(
                candidate, method="HEAD", headers={"User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    if response.status == 200:
                        return url, True
            except urllib.error.HTTPError:
                continue  # try the next mirror
            except Exception:
                break  # a network problem will not improve on another host
        return url, False

    unique = sorted(set(urls))
    if not unique:
        return {}

    print(f"checking {len(unique)} image url(s) across all known mirrors ...")
    health: dict[str, bool] = {}
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for url, ok in pool.map(probe, unique):
            health[url] = ok
    alive = sum(1 for ok in health.values() if ok)
    print(f"  reachable: {alive}/{len(unique)} (tried legacy mirrors too)")
    return health


@dataclass
class ImageReport:
    """Outcome of repairing broken images."""

    checked: int = 0
    alive: int = 0
    removed_tags: int = 0
    dropped_questions: int = 0
    figure_questions: int = 0

    def summary(self) -> str:
        return "\n".join(
            [
                f"image urls checked:      {self.checked}",
                f"reachable:               {self.alive}",
                f"broken image tags removed: {self.removed_tags}",
                f"questions dropped (image was required): {self.dropped_questions}",
                f"questions still referring to a figure:  {self.figure_questions}",
            ]
        )


def repair_images(
    questions: list[dict], health: dict[str, bool]
) -> tuple[list[dict], ImageReport]:
    """Strip dead image tags and drop questions whose visual is gone.

    Two different problems are handled:

    1. an image tag pointing at a URL that no longer resolves, and
    2. a question that refers to a figure or table it does not contain.

    In both cases the question is only discarded when the visual was load-bearing
    — that is, when the remaining text still depends on it. Everything else keeps
    its text and simply loses the broken tag.
    """
    report = ImageReport(checked=len(health), alive=sum(1 for ok in health.values() if ok))
    kept: list[dict] = []

    for question in questions:
        text = question.get("question_text") or ""
        found = _IMG_SRC_RE.findall(text)
        broken = [url for url in found if not health.get(url, False)]
        working = [url for url in found if health.get(url, False)]

        if broken:
            repaired = text
            for url in broken:
                repaired = re.sub(
                    r'<img[^>]*src="' + re.escape(url) + r'"[^>]*>', "", repaired
                )
            repaired = re.sub(r"\s+", " ", repaired).strip()
            report.removed_tags += len(broken)
            question = dict(question)
            question["question_text"] = repaired
            text = repaired

        # With no image left, does the question still make sense on its own?
        if not working:
            reason = needs_missing_visual(text)
            if reason:
                report.dropped_questions += 1
                report.figure_questions += 1
                continue

        kept.append(question)

    return kept, report


# -------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rdojo_import",
        description="Import the rdojo question bank into this app's import format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("action", choices=["list", "fetch"], help="what to do")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="subject slug")
    parser.add_argument("--topic", action="append", help="only these topic slugs (repeatable)")
    parser.add_argument("--limit", type=int, help="stop after N topics (trial runs)")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    parser.add_argument("--output", default=str(OUTPUT_FILE), help="output JSON path")
    parser.add_argument(
        "--check-images",
        action="store_true",
        help="probe every image url and strip the ones that are no longer reachable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.action == "list":
            entries = fetch_index(refresh=args.refresh, subject=args.subject)
            print(f"{args.subject}: {len(entries)} topics\n")
            for entry in entries:
                print(f"  {rdojo.topic_slug_from_path(entry['path']):48} {entry['path']}")
            return 0

        questions, report = fetch_banks(
            subject=args.subject,
            only_topics=args.topic,
            limit=args.limit,
            refresh=args.refresh,
        )
        questions, removed = deduplicate(questions)
        report.duplicates = removed

        # Optionally verify every referenced image before writing, so a
        # question never depends on a diagram that has since disappeared.
        if args.check_images:
            urls: list[str] = []
            for question in questions:
                urls.extend(_IMG_SRC_RE.findall(question.get("question_text") or ""))
            health = check_image_urls(urls)
            questions, image_report = repair_images(questions, health)
            print()
            print(image_report.summary())
            report.questions = len(questions)

        output, review = write_outputs(questions, report, output=Path(args.output))

        print()
        print("=" * 72)
        print(report.summary())
        print("=" * 72)
        print(f"\nwrote {output}")
        if review:
            print(f"review {review}")
        print(
            "\nNext steps:\n"
            f"  flask quiz check {output}\n"
            f"  flask quiz import {output}"
        )
        return 0

    except RdojoError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
