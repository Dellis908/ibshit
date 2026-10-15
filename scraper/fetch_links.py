"""Fetch the PDF list from the source site.

The source page renders its file list with JavaScript, so a plain HTTP request
is not enough (it also returns HTTP 403 to bots). Selenium is used first and a
plain ``requests``-based HTML scan is kept as a fallback so the pipeline still
works on sites that serve static links.

Selenium, webdriver-manager and requests are optional dependencies: import
errors are surfaced as clear instructions rather than tracebacks.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

from . import config

_PDF_HREF_RE = re.compile(r"""href=["']([^"']+\.pdf(?:\?[^"']*)?)["']""", re.IGNORECASE)


class ScrapeError(RuntimeError):
    """Raised when the source page cannot be fetched or parsed."""


def _absolute(url: str, base: str) -> str:
    return urljoin(base, url.strip())


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def find_pdf_links_requests(page_url: str | None = None) -> list[str]:
    """Static HTML fallback: pull ``.pdf`` links out of the raw page source."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ScrapeError("requests is not installed: pip install requests") from exc

    page_url = page_url or config.SOURCE_PAGE
    response = requests.get(
        page_url,
        timeout=config.REQUEST_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    )
    response.raise_for_status()
    links = [_absolute(href, response.url) for href in _PDF_HREF_RE.findall(response.text)]
    return _dedupe(links)


def find_pdf_links_selenium(page_url: str | None = None) -> list[str]:
    """Drive a real browser to collect PDF links from a JS-rendered page."""
    page_url = page_url or config.SOURCE_PAGE

    try:
        from selenium.webdriver.common.by import By
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ScrapeError(
            "selenium is not installed: pip install -r scraper/requirements.txt"
        ) from exc

    driver = _build_driver()
    try:
        driver.get(page_url)
        time.sleep(config.PAGE_LOAD_WAIT)  # let the list render

        links: list[str] = []
        for anchor in driver.find_elements(By.TAG_NAME, "a"):
            href = anchor.get_attribute("href")
            if href and ".pdf" in href.lower():
                links.append(href)

        # Some layouts only expose the URL inside a script or data attribute.
        if not links:
            for element in driver.find_elements(By.CSS_SELECTOR, "[data-href], [data-url]"):
                value = element.get_attribute("data-href") or element.get_attribute("data-url")
                if value and ".pdf" in value.lower():
                    links.append(_absolute(value, page_url))

        base = driver.current_url or page_url
        return _dedupe([_absolute(link, base) for link in links])
    finally:
        driver.quit()


def _build_driver():
    """Create a headless WebDriver, preferring an auto-managed driver."""
    from selenium import webdriver

    if config.BROWSER == "firefox":
        from selenium.webdriver.firefox.options import Options as FirefoxOptions

        options = FirefoxOptions()
        if config.HEADLESS:
            options.add_argument("-headless")
        try:
            return webdriver.Firefox(options=options)
        except Exception as exc:  # pragma: no cover - environment specific
            raise ScrapeError(
                "Could not start Firefox. Install geckodriver or switch "
                "SCRAPER_BROWSER=chrome."
            ) from exc

    from selenium.webdriver.chrome.options import Options as ChromeOptions

    options = ChromeOptions()
    if config.HEADLESS:
        options.add_argument("--headless=new")
    for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1400,2000"):
        options.add_argument(flag)
    options.add_argument(f"--user-agent={config.USER_AGENT}")

    try:
        return webdriver.Chrome(options=options)
    except Exception as first_error:  # pragma: no cover - environment specific
        # Try the driver manager as a second chance, then give clear advice.
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service

            service = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=options)
        except Exception as exc:
            raise ScrapeError(
                "Could not start Chrome. Install Google Chrome and/or run "
                "`pip install -r scraper/requirements.txt` so webdriver-manager can "
                "fetch a driver, or set SCRAPER_BROWSER=firefox. "
                f"Underlying error: {first_error}"
            ) from exc


def get_pdf_links(page_url: str | None = None, *, allow_fallback: bool = True) -> list[str]:
    """Return every PDF link on the source page, preferring a real browser."""
    errors: list[str] = []

    try:
        links = find_pdf_links_selenium(page_url)
        if links:
            return links
        errors.append("selenium found no PDF links on the rendered page")
    except ScrapeError as exc:
        errors.append(str(exc))

    if allow_fallback:
        try:
            links = find_pdf_links_requests(page_url)
            if links:
                return links
            errors.append("the static page source contained no PDF links")
        except Exception as exc:
            errors.append(f"static fetch failed: {exc}")

    raise ScrapeError(
        "Could not collect any PDF links.\n  - " + "\n  - ".join(errors)
    )


def describe(url: str) -> str:
    """A short human-readable label for a PDF URL."""
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] or url
