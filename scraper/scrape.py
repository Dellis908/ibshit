"""Scraper pipeline: fetch PDF links, download, extract text, build questions.

    python -m scraper.scrape links          # just list the PDF URLs found
    python -m scraper.scrape download       # download every PDF
    python -m scraper.scrape parse          # extract text from local PDFs
    python -m scraper.scrape build          # text -> seed/scraped.json
    python -m scraper.scrape all            # download + parse + build
    python -m scraper.scrape build --from-text data/extracted/cell.txt --topic cell-biology

Every stage is resumable: downloads and extractions are cached on disk, so
re-running a command does not repeat finished work.

IMPORTANT: only use this on material you are entitled to access. Automated
access may breach a site's terms of service. The tool reads the source page and
PDFs exactly as a browser would, and you are responsible for how you use it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - `python scraper/scrape.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import build_seed, config, download, fetch_links


# ---------------------------------------------------------------------- stages

def stage_links(args) -> list[str]:
    """Print the PDF links found on the source page."""
    print(f"source: {config.SOURCE_PAGE}")
    links = fetch_links.get_pdf_links(config.SOURCE_PAGE)
    print(f"found {len(links)} PDF links\n")
    for link in links:
        print(f"  {fetch_links.describe(link)}")
        if args.verbose:
            print(f"      {link}")
    return links


def stage_download(args) -> list[Path]:
    """Download every PDF linked from the source page."""
    config.ensure_dirs()
    links = fetch_links.get_pdf_links(config.SOURCE_PAGE)
    if config.MAX_PDFS:
        links = links[: config.MAX_PDFS]
    print(f"downloading {len(links)} PDFs into {config.PDF_DIR}\n")

    downloaded, failures = download.download_all(
        links, directory=config.PDF_DIR, overwrite=args.overwrite
    )
    print(f"\ndownloaded {len(downloaded)} new file(s), {len(failures)} failure(s)")
    for failure in failures:
        print(f"  - {failure}")
    return downloaded


def stage_parse(args) -> dict[str, list[str]]:
    """Extract text from the downloaded PDFs."""
    config.ensure_dirs()
    pdf_paths = sorted(config.PDF_DIR.glob("*.pdf"))
    if not pdf_paths:
        print(f"no PDFs in {config.PDF_DIR} — run the download stage first")
        return {}

    print(f"extracting text from {len(pdf_paths)} PDF(s)\n")
    results = download.extract_all(
        pdf_paths, text_dir=config.TEXT_DIR, overwrite=args.overwrite
    )

    if args.to_seed:
        texts = {name: "\n".join(pages) for name, pages in results.items()}
        _write_build(texts, args)
    return results


def stage_build(args) -> list[dict]:
    """Build the import-ready question file from extracted text."""
    texts: dict[str, str] = {}

    if args.from_text:
        for path_str in args.from_text:
            path = Path(path_str)
            if not path.exists():
                print(f"skipping missing file: {path}")
                continue
            texts[path.name] = path.read_text(encoding="utf-8").replace("\f", "\n")
    else:
        config.ensure_dirs()
        for text_path in sorted(config.TEXT_DIR.glob("*.txt")):
            texts[text_path.name] = text_path.read_text(encoding="utf-8").replace("\f", "\n")

    if not texts:
        print("nothing to build from. Run the parse stage, or pass --from-text FILE.")
        return []

    print(f"parsing {len(texts)} text file(s)\n")
    return _write_build(texts, args)


def _write_build(texts: dict[str, str], args) -> list[dict]:
    overrides: dict[str, str] = {}
    if getattr(args, "topic", None):
        overrides = {name: args.topic for name in texts}

    questions, report = build_seed.build_from_texts(
        texts,
        topic_overrides=overrides,
        confidence_threshold=args.min_confidence,
    )

    output_path = Path(args.output)
    build_seed.write_questions(questions, output_path)

    importable_path = output_path.with_name(output_path.stem + ".importable.json")
    build_seed.write_importable(questions, importable_path)

    low = [q for q in questions if q.get("_confidence", 0) < args.min_confidence]
    review_path = output_path.with_name(output_path.stem + ".review.md")
    if low:
        build_seed.write_review(low, review_path)

    print("=" * 68)
    print(report.summary())
    print("=" * 68)
    print(f"\nfull parse:  {output_path}")
    print(f"importable:  {importable_path}")
    if low:
        print(f"review file: {review_path}")
    print(
        "\nImport the reviewed questions with:\n"
        f"  flask quiz check {importable_path}\n"
        f"  flask quiz import {importable_path}"
    )
    return questions


def stage_all(args) -> None:
    """Run the whole pipeline end to end."""
    stage_download(args)
    print()
    stage_parse(args)
    print()
    stage_build(args)


# ----------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape",
        description="Optional PDF scraping pipeline for the IB Biology quiz.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "stage",
        choices=["links", "download", "parse", "build", "all"],
        help="which part of the pipeline to run",
    )
    parser.add_argument("--output", default=str(config.OUTPUT_FILE), help="output JSON path")
    parser.add_argument(
        "--from-text",
        nargs="*",
        help="build from these text files instead of data/extracted",
    )
    parser.add_argument("--topic", help="force one topic slug for every question built")
    parser.add_argument("--overwrite", action="store_true", help="redo cached downloads/extractions")
    parser.add_argument("--to-seed", action="store_true", help="after parsing, also build the seed file")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.6,
        help="flag questions below this score (0-1)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print full URLs")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handlers = {
        "links": stage_links,
        "download": stage_download,
        "parse": stage_parse,
        "build": stage_build,
        "all": stage_all,
    }

    try:
        handlers[args.stage](args)
    except fetch_links.ScrapeError as exc:
        print(f"\nscrape error: {exc}", file=sys.stderr)
        print(
            "\nIf the site blocks automated access, respect that and use the "
            "--from-text path with PDFs you obtained yourself.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
