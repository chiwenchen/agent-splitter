"""Integration tests for GET /share — the ta-key picker page that lets
the operator copy a tagged share URL to clipboard."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler  # noqa: E402
from ta_mapping import TA_KEYS, TA_SUBTITLES  # noqa: E402


def _share_event(host: str = "split.redarch.dev"):
    return {
        "rawPath": "/share",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": None,
        "headers": {"x-forwarded-host": host, "x-forwarded-proto": "https"},
    }


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_share_picker_returns_200_html():
    resp = handler.lambda_handler(_share_event(), None)
    assert resp["statusCode"] == 200
    assert "text/html" in resp["headers"]["Content-Type"]


def test_share_picker_lists_all_ta_keys():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    for ta_key in TA_KEYS:
        # Each ta key should appear at least once (in the .ta-key label)
        assert ta_key in body, f"ta key {ta_key!r} missing from /share page"


def test_share_picker_shows_zh_tw_subtitles():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    for subtitle in TA_SUBTITLES["zh-TW"].values():
        assert subtitle in body, f"subtitle {subtitle!r} missing from /share"


def test_share_picker_default_url_omits_ta_param():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    # default card should show the bare base URL
    assert "https://split.redarch.dev" in body
    # The default card's URL should NOT include ?ta=default
    # We assert by counting: ?ta=default should not appear anywhere
    assert "?ta=default" not in body


def test_share_picker_non_default_urls_include_ta_param():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    for ta_key in TA_KEYS:
        if ta_key == "default":
            continue
        expected_url = f"https://split.redarch.dev/?ta={ta_key}"
        assert expected_url in body, f"missing URL for ta={ta_key}"


def test_share_picker_includes_copy_helper_script():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    assert "copyToClipboard" in body
    assert "navigator.clipboard" in body


def test_share_picker_uses_forwarded_host():
    event = _share_event(host="custom.example.com")
    resp = handler.lambda_handler(event, None)
    body = resp["body"]
    assert "https://custom.example.com" in body
    assert "https://split.redarch.dev" not in body


def test_share_picker_rejects_post():
    event = _share_event()
    event["requestContext"]["http"]["method"] = "POST"
    resp = handler.lambda_handler(event, None)
    assert resp["statusCode"] == 405


def test_share_picker_does_not_collide_with_v1_share():
    """GET /share is the picker; POST /v1/share is the create-share API.
    They share a prefix but must route independently."""
    # /share GET
    resp = handler.lambda_handler(_share_event(), None)
    assert resp["statusCode"] == 200
    # /v1/share GET should NOT be routed to the picker — it's POST-only
    event = {
        "rawPath": "/v1/share",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": None,
        "headers": {},
    }
    resp = handler.lambda_handler(event, None)
    assert resp["statusCode"] == 405  # method not allowed


# ---------------------------------------------------------------------------
# Card count integrity
# ---------------------------------------------------------------------------


def test_share_picker_card_count_matches_ta_keys():
    resp = handler.lambda_handler(_share_event(), None)
    body = resp["body"]
    card_count = body.count('class="ta-card"')
    assert card_count == len(TA_KEYS), (
        f"expected {len(TA_KEYS)} cards, found {card_count}"
    )
