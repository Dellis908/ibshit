"""HTML/text sanitising for question content.

Question text is rendered with ``|safe`` in the templates (IB questions need
subscripts and occasional tables), so anything that reaches the database must
be sanitised first. This module implements a small allow-list sanitiser built
on the standard library only.

Two levels of strictness are available:

* :func:`sanitize_html` keeps a small set of formatting tags and attributes
  and removes every script/event handler/style/URL.
* :func:`to_plain_text` returns plain text with tags stripped and entities
  decoded, for places where markup is not wanted (JSON exports, emails).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

#: Formatting tags that questions are allowed to use.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "sub", "sup", "b", "strong", "i", "em", "u", "br", "p", "span",
        "ul", "ol", "li", "table", "tr", "td", "th", "thead", "tbody",
        "img",
    }
)

#: Tags whose entire content must be dropped, not just the tag itself.
DROP_CONTENT_TAGS: frozenset[str] = frozenset(
    {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg"}
)

#: Attributes that are safe on every allowed tag.
ALLOWED_GLOBAL_ATTRS: frozenset[str] = frozenset({"class"})

#: Additional attributes allowed per tag.
ALLOWED_TAG_ATTRS: dict[str, frozenset[str]] = {
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
    "img": frozenset({"src", "alt"}),
}

#: Hosts an <img> in question content may point at. Anything else (including
#: protocol-relative and data: URLs) is dropped.
ALLOWED_IMAGE_HOSTS: frozenset[str] = frozenset(
    {"pub-images.revisiondojo.com", "localhost", "127.0.0.1"}
)

_DANGEROUS_URL_RE = re.compile(r"^\s*(javascript|vbscript|data|file)\s*:", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class _Sanitizer(HTMLParser):
    """Rebuild a document keeping only allow-listed tags and attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop_depth = 0
        self._open: list[str] = []

    # ------------------------------------------------------------- HTMLParser
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag not in ALLOWED_TAGS:
            return

        safe_attrs = self._filter_attrs(tag, attrs)
        self._out.append(f"<{tag}{safe_attrs}>")
        if tag not in {"br"}:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self._drop_depth or tag in DROP_CONTENT_TAGS or tag not in ALLOWED_TAGS:
            return
        self._out.append(f"<{tag}{self._filter_attrs(tag, attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth or tag not in ALLOWED_TAGS or tag == "br":
            return
        # Only close tags we actually opened, and close any left dangling inside.
        if tag in self._open:
            while self._open:
                open_tag = self._open.pop()
                self._out.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self._out.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:  # noqa: D102 - comments dropped
        return

    def handle_decl(self, decl: str) -> None:  # noqa: D102 - doctypes dropped
        return

    def handle_pi(self, data: str) -> None:  # noqa: D102 - processing instructions dropped
        return

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _filter_attrs(tag: str, attrs) -> str:
        allowed = ALLOWED_GLOBAL_ATTRS | ALLOWED_TAG_ATTRS.get(tag, frozenset())
        parts: list[str] = []
        for name, value in attrs:
            name = (name or "").lower()
            value = "" if value is None else str(value)
            if name.startswith("on") or name in {"style", "srcset", "formaction"}:
                continue
            if name not in allowed:
                continue
            if _DANGEROUS_URL_RE.match(value):
                continue
            if name == "src" and not _image_src_allowed(value):
                continue
            parts.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(parts)

    def result(self) -> str:
        # Close anything still open so the fragment is well formed.
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out)


def _image_src_allowed(value: str) -> bool:
    """Whether an image URL is safe and on an allowed host."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_IMAGE_HOSTS)


def text_with_images(value: str | None) -> str:
    """Plain text, but with image URLs preserved.

    Used when deciding whether two options are genuinely different: image-based
    choices carry no words, so their URLs are the only distinguishing feature.
    """
    if not value:
        return ""
    expanded = re.sub(
        r"<img[^>]*src=['\"]([^'\"]+)['\"][^>]*>",
        lambda m: f" [image:{m.group(1)}] ",
        str(value),
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", _strip_tags(expanded)).strip()


def sanitize_html(value: str | None) -> str:
    """Return an HTML fragment containing only safe formatting markup."""
    if not value:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(str(value))
        parser.close()
    except Exception:  # pragma: no cover - defensive: never 500 on bad markup
        return html.escape(_strip_tags(str(value)))
    cleaned = parser.result()
    cleaned = cleaned.replace("\x00", "")
    cleaned = _WS_RE.sub(" ", cleaned)
    cleaned = _BLANK_LINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value)


def to_plain_text(value: str | None) -> str:
    """Return plain text: tags removed, entities decoded, whitespace tidied."""
    if not value:
        return ""
    text = _strip_tags(str(value))
    text = html.unescape(text)
    text = text.replace("<br>", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_plain(value: str | None, *, max_length: int | None = None) -> str:
    """Sanitise a value that must contain no markup at all."""
    text = to_plain_text(sanitize_html(value))
    if max_length is not None:
        text = text[:max_length]
    return text
