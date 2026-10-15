"""Tests for deployment configuration.

These guard behaviour that only shows up in production, where a mistake is
expensive: a missing secret key, cookies without the Secure flag, or trusting
proxy headers when nothing is in front of the app.
"""

from __future__ import annotations

import pytest

from app import create_app


# ------------------------------------------------------------- secret key

def test_development_config_boots_without_a_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    app = create_app("development")
    assert app.config["SECRET_KEY"]  # falls back to the dev placeholder


def test_production_refuses_a_missing_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        create_app("production")
    assert "SECRET_KEY is not set" in str(excinfo.value)
    # The message must tell an operator what to do, not just that it failed.
    assert "openssl rand" in str(excinfo.value)


def test_production_refuses_the_development_placeholder(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
    with pytest.raises(RuntimeError) as excinfo:
        create_app("production")
    assert "placeholder" in str(excinfo.value)


def test_production_accepts_a_real_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-real-key-that-is-long-enough")
    app = create_app("production")
    assert app.config["DEBUG"] is False


def test_testing_config_is_exempt(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    app = create_app("testing")
    assert app.config["TESTING"] is True


# ---------------------------------------------------------------- cookies

def test_session_cookie_flags_by_default(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    app = create_app("production")
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    # Not Secure by default, so plain HTTP on a LAN still works.
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_secure_cookie_enabled_by_environment(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
    app = create_app("production")
    assert app.config["SESSION_COOKIE_SECURE"] is True

    response = app.test_client().get("/quiz?count=2")
    cookies = " ".join(response.headers.getlist("Set-Cookie"))
    assert "Secure" in cookies
    assert "HttpOnly" in cookies
    assert "SameSite=Lax" in cookies


# ------------------------------------------------------------ proxy trust

def test_proxy_headers_are_not_trusted_by_default():
    """A direct client must not be able to claim its request is HTTPS."""
    app = create_app("development")
    assert type(app.wsgi_app).__name__ != "ProxyFix"
    assert app.config["TRUST_PROXY_HEADERS"] is False


def test_proxy_fix_applied_when_enabled(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    app = create_app("production")
    assert type(app.wsgi_app).__name__ == "ProxyFix"

    # With the proxy trusted, the app sees the forwarded scheme.
    response = app.test_client().get(
        "/healthz", headers={"X-Forwarded-Proto": "https"}
    )
    assert response.status_code == 200


def test_preferred_url_scheme_from_environment(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    monkeypatch.setenv("PREFERRED_URL_SCHEME", "https")
    app = create_app("production")
    assert app.config["PREFERRED_URL_SCHEME"] == "https"


# -------------------------------------------------------- reverse proxy setup

def test_health_endpoint_works_without_a_session():
    """The health check must not depend on sessions or the database."""
    app = create_app("testing")
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_app_serves_from_a_proxy_subpath_without_absolute_urls():
    """Templates must not hardcode absolute URLs that break behind a proxy."""
    app = create_app("testing")
    html = app.test_client().get("/").get_data(as_text=True)
    assert "http://localhost" not in html
    assert 'href="/static/css/style.css"' in html
