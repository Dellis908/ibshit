"""Locating question images across the hosts the question bank has used.

The bank's image URLs point at object storage that has been migrated more than
once. The same object identifier has appeared under several hosts and path
shapes over time:

===========================================================  ==================
Host / path                                                  Status observed
===========================================================  ==================
``pub-images.revisiondojo.com/question-images/<id>``          works, sometimes
``s.revisiondojo.com/storage/v1/object/public/question-images/<id>``
                                                              works, sometimes
``<project>.supabase.co/storage/v1/object/public/...``        project deleted
``pub-images.revisiondojo.com/question-images/<id>.jpeg``     usually gone
``pub-images.revisiondojo.com/plots/<id>.png``                usually gone
===========================================================  ==================

So an image that 404s on the URL recorded in the bank may still exist on another
host. This module derives every candidate URL for a given image and lets the
image proxy try them in order, which recovers images the bank alone would drop.

It also reports *why* an image cannot be served, so a missing figure is a
diagnosable state rather than a mystery.

No network access happens here: the candidates are pure string derivation, which
makes them easy to test. The proxy performs the requests.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

#: Primary host used by the current bank.
IMAGE_HOST = "pub-images.revisiondojo.com"

#: Legacy mirrors that have served the same objects.
LEGACY_IMAGE_HOSTS = ("s.revisiondojo.com",)

#: All hosts we are willing to fetch an image from.
ALLOWED_IMAGE_HOSTS = frozenset({IMAGE_HOST, *LEGACY_IMAGE_HOSTS})

#: Legacy Supabase projects are dead, but recognising them lets us explain why
#: rather than reporting a generic failure.
_DEAD_HOST_RE = re.compile(r"^[a-z0-9]+\.supabase\.co$", re.IGNORECASE)

#: The object identifier inside any of the known path shapes.
_OBJECT_ID_RE = re.compile(
    r"""
    (?:
        /question-images/            # current and legacy question images
        | /plots/                    # generated data plots
        | /notes/                    # note images
    )
    (?P<object>[A-Za-z0-9._-]+?)
    (?:\.(?:jpe?g|png|webp|gif|svg))?    # an extension, when present
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: Recognised file extensions, stripped when deriving a bare object id.
_EXTENSION_RE = re.compile(r"\.(jpe?g|png|webp|gif|svg)$", re.IGNORECASE)


class ImageUnavailable(RuntimeError):
    """Raised when an image cannot be fetched from any candidate host."""


def host_of(url: str) -> str:
    """The lower-case hostname of a URL, or an empty string."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_dead_host(url: str) -> bool:
    """Whether a URL points at a host that no longer exists.

    Recognising retired hosts lets the proxy rewrite those URLs onto a live
    mirror instead of rejecting them outright.
    """
    return bool(_DEAD_HOST_RE.match(host_of(url)))


def is_supported_host(url: str) -> bool:
    """Whether we are willing to fetch images from this URL's host."""
    host = host_of(url)
    return any(host == allowed for allowed in ALLOWED_IMAGE_HOSTS)


def object_id(url: str) -> str | None:
    """The bare object identifier inside a known image URL.

    ``.../question-images/QP62.jpeg`` -> ``QP62``
    """
    path = urlparse(url).path or ""
    match = _OBJECT_ID_RE.search(path)
    if not match:
        return None
    return match.group("object")


def _kind(url: str) -> str:
    """The bucket a URL refers to: question-images, plots or notes."""
    for kind in ("question-images", "plots", "notes"):
        if f"/{kind}/" in (urlparse(url).path or ""):
            return kind
    return "question-images"


def candidates(url: str, *, include_dead: bool = False) -> list[str]:
    """Every URL that might serve the same image, best first.

    The recorded URL is tried first so working links are unaffected; legacy
    mirrors follow. Dead Supabase URLs are included only when explicitly asked
    for, since that project no longer resolves.
    """
    if not url:
        return []

    host = host_of(url)
    if host and _DEAD_HOST_RE.match(host):
        if not include_dead:
            # Nothing on that host resolves any more; there is no point trying.
            stripped = _strip_to_object(url)
            return [stripped] if stripped else []
        return [url]

    oid = object_id(url)
    if not oid:
        # The URL does not match a known shape (for example a plain UUID with a
        # `.jpeg` suffix). Fall back to the URL itself so nothing breaks.
        return [url]

    kind = _kind(url)
    ordered: list[str] = []

    def add(candidate: str) -> None:
        if candidate not in ordered:
            ordered.append(candidate)

    # The URL as recorded, then the same object without an extension, then the
    # legacy mirrors in both shapes.
    add(url)
    add(f"https://{IMAGE_HOST}/{kind}/{oid}")
    for legacy in LEGACY_IMAGE_HOSTS:
        add(f"https://{legacy}/storage/v1/object/public/{kind}/{oid}")
        add(f"https://{legacy}/{kind}/{oid}")

    return ordered


def _strip_to_object(url: str) -> str | None:
    """Rewrite a dead Supabase URL onto the live host, if possible."""
    oid = object_id(url)
    if not oid:
        return None
    return f"https://{IMAGE_HOST}/{_kind(url)}/{oid}"


def explain(url: str) -> str:
    """A human-readable reason an image cannot be shown."""
    host = host_of(url)
    if host and _DEAD_HOST_RE.match(host):
        return "the image host that served this figure no longer exists"
    if "/plots/" in url:
        return "this data plot is no longer available from the question source"
    if "/notes/" in url:
        return "this note image is no longer available from the question source"
    return "this figure is no longer available from the question source"
