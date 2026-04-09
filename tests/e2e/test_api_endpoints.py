"""E2E tests: API endpoints (health, OpenAPI, docs, 404)."""

import re

from playwright.sync_api import Page, expect


def test_health(page: Page, base_url: str) -> None:
    """GET /health returns {"status": "ok"}."""
    resp = page.goto(f"{base_url}/health")
    assert resp.status == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_openapi_json(page: Page, base_url: str) -> None:
    """GET /openapi.json returns valid OpenAPI schema."""
    resp = page.goto(f"{base_url}/openapi.json")
    assert resp.status == 200
    body = resp.json()
    assert "openapi" in body
    assert body["openapi"].startswith("3.")


def test_docs_page(page: Page, base_url: str) -> None:
    """GET /docs loads a page with Split Senpai in the title."""
    page.goto(f"{base_url}/docs")
    expect(page).to_have_title(re.compile(r"Senpai|Split"))


def test_404_share(page: Page, base_url: str) -> None:
    """GET /s/nonexistent-id returns 404."""
    resp = page.goto(f"{base_url}/s/this-does-not-exist-99999")
    assert resp.status == 404
