"""Download PDFs and extract their text."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config


class DownloadError(RuntimeError):
    """Raised when a PDF cannot be downloaded."""


def filename_for(url: str) -> str:
    """Derive a safe local filename from a PDF URL."""
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1] or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    # Strip anything that could escape the target directory.
    return "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip() or "document.pdf"


def download_pdf(
    url: str,
    *,
    directory: Path | None = None,
    overwrite: bool = False,
    session=None,
) -> Path | None:
    """Download one PDF. Returns the path, or ``None`` if it already existed."""
    directory = Path(directory or config.PDF_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename_for(url)

    if target.exists() and not overwrite and target.stat().st_size > 0:
        return None

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise DownloadError("requests is not installed: pip install requests") from exc

    client = session or requests
    response = client.get(
        url,
        timeout=config.REQUEST_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
        stream=True,
    )
    if response.status_code != 200:
        raise DownloadError(f"HTTP {response.status_code} for {url}")

    # Write to a temporary file first so a failed download never leaves a
    # truncated PDF that later looks like a successful one.
    tmp = target.with_suffix(target.suffix + ".part")
    with open(tmp, "wb") as handle:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                handle.write(chunk)
    tmp.replace(target)
    return target


def download_all(
    urls: list[str],
    *,
    directory: Path | None = None,
    overwrite: bool = False,
) -> tuple[list[Path], list[str]]:
    """Download every URL. Returns ``(downloaded, failures)``."""
    downloaded: list[Path] = []
    failures: list[str] = []
    session = None
    try:
        import requests

        session = requests.Session()
    except ImportError:  # pragma: no cover - optional dependency
        session = None

    for index, url in enumerate(urls, start=1):
        label = f"[{index}/{len(urls)}]"
        try:
            path = download_pdf(url, directory=directory, overwrite=overwrite, session=session)
            if path is None:
                print(f"{label} cached  {filename_for(url)}")
            else:
                print(f"{label} saved   {path.name} ({path.stat().st_size // 1024} KB)")
                downloaded.append(path)
        except Exception as exc:
            print(f"{label} FAILED  {url} — {exc}")
            failures.append(f"{url}: {exc}")
        time.sleep(config.REQUEST_DELAY)

    if session is not None:
        session.close()
    return downloaded, failures


# ----------------------------------------------------------------- extraction

def extract_with_pdfplumber(pdf_path: Path) -> list[str]:
    """Extract text page by page using pdfplumber (best layout fidelity)."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Two-column exam papers need column-aware extraction.
            text = _extract_page_columns(page)
            pages.append(text or "")
    return pages


def _extract_page_columns(page) -> str:
    """Extract a page's text, splitting two-column layouts correctly."""
    try:
        width = float(page.width)
        midpoint = width * config.COLUMN_SPLIT_X
        left = page.crop((0, 0, midpoint, page.height)).extract_text() or ""
        right = page.crop((midpoint, 0, width, page.height)).extract_text() or ""
        if left.strip() and right.strip():
            return f"{left}\n{right}"
        return left or right or (page.extract_text() or "")
    except Exception:
        return page.extract_text() or ""


def extract_with_pymupdf(pdf_path: Path) -> list[str]:
    """Fallback extraction with PyMuPDF."""
    import fitz  # PyMuPDF

    pages: list[str] = []
    with fitz.open(pdf_path) as document:
        for page in document:
            pages.append(page.get_text("text") or "")
    return pages


def extract_text(pdf_path: Path) -> list[str]:
    """Extract text from a PDF, trying pdfplumber then PyMuPDF."""
    pdf_path = Path(pdf_path)
    errors: list[str] = []

    try:
        pages = extract_with_pdfplumber(pdf_path)
        if any(page.strip() for page in pages):
            return pages
        errors.append("pdfplumber returned no text (scanned image?)")
    except ImportError:
        errors.append("pdfplumber is not installed")
    except Exception as exc:
        errors.append(f"pdfplumber failed: {exc}")

    try:
        pages = extract_with_pymupdf(pdf_path)
        if any(page.strip() for page in pages):
            return pages
        errors.append("PyMuPDF returned no text (scanned image?)")
    except ImportError:
        errors.append("PyMuPDF is not installed")
    except Exception as exc:
        errors.append(f"PyMuPDF failed: {exc}")

    raise DownloadError(f"could not extract text from {pdf_path.name}: " + "; ".join(errors))


def extract_all(
    paths: list[Path],
    *,
    text_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, list[str]]:
    """Extract text for every PDF, caching the result as ``<name>.txt``."""
    text_dir = Path(text_dir or config.TEXT_DIR)
    text_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, list[str]] = {}

    pdf_paths = sorted(Path(p) for p in (paths or config.PDF_DIR.glob("*.pdf")))
    for index, pdf_path in enumerate(pdf_paths, start=1):
        cache = text_dir / f"{pdf_path.stem}.txt"
        if cache.exists() and not overwrite:
            results[pdf_path.name] = cache.read_text(encoding="utf-8").split("\f")
            print(f"[{index}/{len(pdf_paths)}] cached  {cache.name}")
            continue
        try:
            pages = extract_text(pdf_path)
        except Exception as exc:
            print(f"[{index}/{len(pdf_paths)}] FAILED  {pdf_path.name} — {exc}")
            continue
        cache.write_text("\f".join(pages), encoding="utf-8")
        results[pdf_path.name] = pages
        print(f"[{index}/{len(pdf_paths)}] text    {pdf_path.name} ({sum(len(p) for p in pages)} chars)")

    return results
