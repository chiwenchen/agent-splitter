"""E2E tests: Admin dashboard."""

import pytest
from playwright.sync_api import Page


def test_admin_unauthenticated_redirects(page: Page, base_url: str) -> None:
    """Unauthenticated request to /admin should be redirected by Cloudflare Access."""
    resp = page.goto(f"{base_url}/admin")
    # Cloudflare Access intercepts and redirects to login (302 -> 200 on login page)
    # or the Lambda returns 401/403. Either way, we should NOT get a 200 with admin HTML.
    url = page.url
    # If CF Access is active, URL will contain cloudflareaccess.com
    # If CF Access is bypassed, Lambda returns 401
    is_redirected = "cloudflareaccess.com" in url
    is_rejected = resp.status in (401, 403)
    assert is_redirected or is_rejected, (
        f"Expected redirect to CF Access or 401/403, got status={resp.status} url={url}"
    )


@pytest.mark.skip(reason="Requires Cloudflare Access JWT — cannot authenticate in E2E")
def test_admin_loads(page: Page, base_url: str) -> None:
    """Admin dashboard should load with stats."""
    page.goto(f"{base_url}/admin")


@pytest.mark.skip(reason="Requires Cloudflare Access JWT — cannot authenticate in E2E")
def test_admin_api_stats(page: Page, base_url: str) -> None:
    """Admin stats API returns aggregate data."""
    page.goto(f"{base_url}/admin/api/stats")
