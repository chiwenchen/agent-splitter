"""E2E tests: Expense flow (add participants, calculator, settlement)."""

from typing import List

from playwright.sync_api import Page, expect


def _add_participants(page: Page, base_url: str, names: List[str]) -> None:
    """Navigate to homepage and add participants by name."""
    page.goto(base_url)
    inp = page.locator("input[placeholder]").first
    for i, name in enumerate(names):
        inp.fill(name)
        inp.press("Enter")
        # Wait for chip to appear before adding the next one
        expect(page.locator(".chip")).to_have_count(i + 1)


def test_add_participants(page: Page, base_url: str) -> None:
    """Adding 2 names via Enter creates 2 chips."""
    _add_participants(page, base_url, ["Alice", "Bob"])
    expect(page.locator(".chip")).to_have_count(2)


def test_add_expense_via_calculator(page: Page, base_url: str) -> None:
    """Open calculator, type 1200, confirm — expense card appears."""
    _add_participants(page, base_url, ["Alice", "Bob"])

    # Open add-expense
    page.locator(".btn-add-hint").click()
    expect(page.locator(".calc-key").first).to_be_visible()

    # Type 1200 on calculator keypad
    for digit in ["1", "2", "0", "0"]:
        page.locator(f"button.calc-key:has-text('{digit}')").first.click()

    # Verify input value
    expect(page.locator("#amt-input")).to_have_value("1200")

    # Confirm (button text varies by language)
    page.locator("button.btn:not(.btn-outline)").first.click()

    # Expense card should appear
    expect(page.locator(".expense-card")).to_have_count(1)


def test_settlement_appears(page: Page, base_url: str) -> None:
    """After adding an expense, settlement result and receipt box appear."""
    _add_participants(page, base_url, ["Alice", "Bob"])

    # Add expense via calculator
    page.locator(".btn-add-hint").click()
    expect(page.locator(".calc-key").first).to_be_visible()
    for digit in ["5", "0", "0"]:
        page.locator(f"button.calc-key:has-text('{digit}')").first.click()
    page.locator("button.btn:not(.btn-outline)").first.click()

    # Settlement result items
    expect(page.locator(".result-item").first).to_be_visible()

    # Receipt box
    expect(page.locator(".receipt-box")).to_be_visible()
