"""Tests for question-image location and mirror fallback.

The question bank's image URLs point at object storage that has been migrated
more than once, so the same object can surface under several hosts. These tests
pin the URL derivation, the host allow-list, and the reason messages — all
without touching the network.
"""

from __future__ import annotations

import pytest

from app import images


# ---------------------------------------------------------------- host checks

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://pub-images.revisiondojo.com/question-images/abc", True),
        ("https://s.revisiondojo.com/storage/v1/object/public/question-images/abc", True),
        ("https://evil.example/x.png", False),
        ("https://cdn.sanity.io/images/x/y.jpg", False),
        ("", False),
    ],
)
def test_is_supported_host(url, expected):
    assert images.is_supported_host(url) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://wccyusoueahdpdicsyrg.supabase.co/storage/v1/object/public/x", True),
        ("https://pub-images.revisiondojo.com/x", False),
        ("https://s.revisiondojo.com/x", False),
    ],
)
def test_is_dead_host(url, expected):
    assert images.is_dead_host(url) is expected


def test_host_of_handles_junk():
    assert images.host_of("not a url") == ""
    assert images.host_of("") == ""


# ---------------------------------------------------------------- object ids

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://pub-images.revisiondojo.com/question-images/QP62.jpeg", "QP62"),
        ("https://pub-images.revisiondojo.com/question-images/abc-123", "abc-123"),
        ("https://pub-images.revisiondojo.com/plots/951b9b46.png", "951b9b46"),
        ("https://pub-images.revisiondojo.com/notes/abc.jpeg", "abc"),
        ("https://example.org/nothing/here", None),
    ],
)
def test_object_id(url, expected):
    assert images.object_id(url) == expected


# ------------------------------------------------------------------ candidates

def test_candidates_include_the_recorded_url_first():
    """A working URL must never be replaced by a mirror guess."""
    url = "https://pub-images.revisiondojo.com/question-images/abc-123"
    assert images.candidates(url)[0] == url


def test_candidates_include_the_legacy_mirror():
    url = "https://pub-images.revisiondojo.com/question-images/abc-123"
    found = images.candidates(url)
    assert any("s.revisiondojo.com/storage/v1/object/public" in c for c in found)
    assert len(found) >= 3


def test_candidates_add_an_extensionless_variant():
    """The same object is stored both with and without a file extension."""
    url = "https://pub-images.revisiondojo.com/question-images/abc-123.jpeg"
    found = images.candidates(url)
    assert "https://pub-images.revisiondojo.com/question-images/abc-123" in found


def test_candidates_rewrite_dead_supabase_urls():
    """A URL on the deleted host is rewritten onto a live one."""
    url = (
        "https://wccyusoueahdpdicsyrg.supabase.co/storage/v1/object/public/"
        "question-images/24d758c0-d870-42e8-bf4f-d6a490ec434e"
    )
    found = images.candidates(url)
    assert found, "a dead-host URL should still produce a candidate"
    assert all("supabase.co" not in c for c in found)
    assert "https://pub-images.revisiondojo.com/question-images/24d758c0-d870-42e8-bf4f-d6a490ec434e" in found


def test_candidates_are_deduplicated():
    url = "https://pub-images.revisiondojo.com/question-images/abc-123"
    found = images.candidates(url)
    assert len(found) == len(set(found))


def test_candidates_keep_the_bucket_kind():
    """A plot must not be looked for under question-images."""
    url = "https://pub-images.revisiondojo.com/plots/abc-123.png"
    found = images.candidates(url)
    assert any("/plots/" in c for c in found)
    assert not any("question-images" in c for c in found)


def test_candidates_handle_unknown_shapes_gracefully():
    """Anything unrecognised is passed through rather than dropped."""
    url = "https://pub-images.revisiondojo.com/weird/path.dat"
    assert images.candidates(url) == [url]


def test_candidates_handle_empty_input():
    assert images.candidates("") == []


# ------------------------------------------------------------------ explain

@pytest.mark.parametrize(
    "url,fragment",
    [
        ("https://abc.supabase.co/storage/v1/object/public/x", "no longer exists"),
        ("https://pub-images.revisiondojo.com/plots/x.png", "plot"),
        ("https://pub-images.revisiondojo.com/notes/x.jpeg", "note"),
        ("https://pub-images.revisiondojo.com/question-images/x", "figure"),
    ],
)
def test_explain_is_specific(url, fragment):
    """A missing figure must produce a diagnosable reason, not a bare 404."""
    assert fragment in images.explain(url)


# ------------------------------------------------------------------- sanitiser

def test_sanitiser_allows_the_legacy_mirror():
    """Images must be accepted from every host the proxy can fetch from."""
    import app.images as image_module
    from app.sanitize import _image_src_allowed

    for host in image_module.ALLOWED_IMAGE_HOSTS:
        assert _image_src_allowed(f"https://{host}/question-images/abc")


def test_sanitiser_and_proxy_share_one_host_list():
    """The allow-list must not be duplicated in two places."""
    import app.sanitize as sanitize_module

    for host in images.ALLOWED_IMAGE_HOSTS:
        assert host in sanitize_module.ALLOWED_IMAGE_HOSTS
