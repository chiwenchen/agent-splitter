"""E2E test configuration for Split Senpai."""

import json
import os
from typing import Any, Dict, Generator

import pytest
from playwright.sync_api import Page

BASE_URL = os.environ.get("TEST_URL", "https://split.redarch.dev")

SHARE_PAYLOAD: Dict[str, Any] = {
    "currency": "TWD",
    "participants": ["Alice", "Bob"],
    "expenses": [
        {"paid_by": "Alice", "amount": 300, "split_among": ["Alice", "Bob"]},
    ],
}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: Dict[str, Any]) -> Dict[str, Any]:
    """Mobile-first viewport (iPhone 14 size)."""
    return {
        **browser_context_args,
        "viewport": {"width": 390, "height": 844},
    }


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture()
def page(page: Page) -> Page:
    """Override default page fixture with a longer default timeout."""
    page.set_default_timeout(15_000)
    return page


@pytest.fixture(scope="module")
def shared_share_id(browser: Any, base_url: str) -> Generator[str, None, None]:
    """Create a share once per module, yield its ID, then clean up."""
    context = browser.new_context()
    p = context.new_page()
    p.goto(base_url)
    p.wait_for_load_state("networkidle")

    result = p.evaluate(
        """async (payload) => {
            const r = await fetch('/v1/share', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            return await r.json();
        }""",
        SHARE_PAYLOAD,
    )
    share_id = result["share_id"]
    p.close()
    context.close()

    yield share_id

    # Teardown: best-effort delete via admin API (won't work without JWT, but
    # shares have TTL so this is acceptable)
