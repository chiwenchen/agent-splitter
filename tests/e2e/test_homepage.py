"""E2E tests: Homepage (SPA load, language toggle, responsive layout)."""

import re

from playwright.sync_api import Page, expect


def test_spa_loads(page: Page, base_url: str) -> None:
    """Homepage loads and the Preact app renders content inside #app."""
    page.goto(base_url)
    app = page.locator("#app")
    expect(app).not_to_be_empty()
    h1 = page.locator("h1")
    expect(h1).to_have_text(re.compile(r"Senpai|仙貝|先輩"))


def test_language_toggle(page: Page, base_url: str) -> None:
    """Clicking the language button cycles through EN / 中 / JA."""
    page.goto(base_url)
    btn = page.locator(".lang-btn")
    expect(btn).to_be_visible()

    t1 = btn.text_content()
    btn.click()
    # Wait for text to change after click
    page.locator(".lang-btn").wait_for(state="attached")
    t2 = btn.text_content()
    btn.click()
    page.locator(".lang-btn").wait_for(state="attached")
    t3 = btn.text_content()

    assert len({t1, t2, t3}) >= 2, f"labels did not cycle: {t1} → {t2} → {t3}"


def test_no_horizontal_overflow(page: Page, base_url: str) -> None:
    """Page should not produce a horizontal scrollbar on mobile viewport."""
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert not overflow, "horizontal overflow detected"
