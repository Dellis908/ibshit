"""Configuration for the optional scraper pipeline.

``BASE_URL`` and ``SOURCE_PAGE`` come from the environment so nothing is
hardcoded. The default source is the *public PDF list page* that the original
author used; point it somewhere else if you prefer::

    export SCRAPER_SOURCE_URL="https://example.org/biology/topic-questions/pdf-list/"
    export SCRAPER_BASE_URL="https://example.org"

Only scrape material you are authorised to access, and check the site's terms
of service before automating against it.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Directory that holds the repo (``scraper/config.py`` -> repo root).
BASE_DIR = Path(__file__).resolve().parent.parent

#: Where PDFs, extracted text and generated question files live.
DATA_DIR = Path(os.getenv("SCRAPER_DATA_DIR", BASE_DIR / "data"))
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "extracted"
OUTPUT_FILE = Path(os.getenv("SCRAPER_OUTPUT", BASE_DIR / "seed" / "scraped.json"))
RAW_OUTPUT_FILE = DATA_DIR / "extracted" / "raw_questions.json"

#: Site to scrape.
SOURCE_PAGE = os.getenv(
    "SCRAPER_SOURCE_URL",
    "https://smearchive.pirateib.su/dp/biology/hl/25/topic-questions/pdf-list/",
)
BASE_URL = os.getenv("SCRAPER_BASE_URL", "https://smearchive.pirateib.su")

#: Requests / driver behaviour.
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
)
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", 60))
REQUEST_DELAY = float(os.getenv("SCRAPER_DELAY", 1.5))
MAX_PDFS = int(os.getenv("SCRAPER_MAX_PDFS", 0)) or None
PAGE_LOAD_WAIT = float(os.getenv("SCRAPER_PAGE_WAIT", 8))
HEADLESS = os.getenv("SCRAPER_HEADLESS", "1") not in {"0", "false", "no"}

#: Browser to drive with Selenium ("chrome" or "firefox").
BROWSER = os.getenv("SCRAPER_BROWSER", "chrome").lower()

#: PDF text layout.
COLUMN_SPLIT_X = float(os.getenv("SCRAPER_COLUMN_SPLIT_X", 0.5))
LINE_TOLERANCE = float(os.getenv("SCRAPER_LINE_TOLERANCE", 3.0))


def ensure_dirs() -> None:
    """Create the data directories if they do not exist."""
    for directory in (DATA_DIR, PDF_DIR, TEXT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
