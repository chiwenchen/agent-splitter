"""E2E tests: Share creation and viewing."""

from playwright.sync_api import Page, expect


def test_create_and_view_share(page: Page, base_url: str, shared_share_id: str) -> None:
    """Navigate to the share page created by the fixture and verify content."""
    page.goto(f"{base_url}/s/{shared_share_id}")
    page.wait_for_load_state("networkidle")
    body_text = page.locator("body").text_content()
    assert "Alice" in body_text
    assert "Bob" in body_text


def test_share_json_api(page: Page, base_url: str, shared_share_id: str) -> None:
    """GET /v1/share/{id} returns JSON with currency and settlements."""
    page.goto(base_url)
    page.wait_for_load_state("networkidle")

    share_id = shared_share_id
    json_result = page.evaluate(
        """async (id) => {
            const r = await fetch('/v1/share/' + id);
            return { status: r.status, body: await r.json() };
        }""",
        share_id,
    )

    assert json_result["status"] == 200
    body = json_result["body"]
    assert body["request_body"]["currency"] == "TWD"
    assert "settlements" in body["result"]
