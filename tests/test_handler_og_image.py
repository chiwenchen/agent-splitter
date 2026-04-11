"""Integration tests for GET /og-image.png — verifies content-type, base64
encoding flag, and 404 fallback when the asset is missing."""
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler  # noqa: E402


def _og_event():
    return {
        "rawPath": "/og-image.png",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": None,
        "headers": {"x-forwarded-host": "split.redarch.dev"},
    }


# ---------------------------------------------------------------------------
# Asset present
# ---------------------------------------------------------------------------


def test_og_image_returns_200_when_asset_present(monkeypatch):
    # Use a tiny valid PNG (1x1 transparent pixel) as a stub
    fake_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\x0d\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    monkeypatch.setattr(handler, "_OG_IMAGE_B64", base64.b64encode(fake_png).decode())

    resp = handler.lambda_handler(_og_event(), None)

    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "image/png"
    assert resp["isBase64Encoded"] is True
    # Cache header
    assert "Cache-Control" in resp["headers"]
    assert "immutable" in resp["headers"]["Cache-Control"]
    # X-Content-Type-Options for security
    assert resp["headers"]["X-Content-Type-Options"] == "nosniff"

    # Decoded body has PNG signature
    decoded = base64.b64decode(resp["body"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Asset missing
# ---------------------------------------------------------------------------


def test_og_image_returns_404_when_asset_missing(monkeypatch):
    monkeypatch.setattr(handler, "_OG_IMAGE_B64", "")

    resp = handler.lambda_handler(_og_event(), None)

    assert resp["statusCode"] == 404
    assert "not found" in resp["body"].lower()


# ---------------------------------------------------------------------------
# Method validation
# ---------------------------------------------------------------------------


def test_og_image_rejects_post(monkeypatch):
    monkeypatch.setattr(handler, "_OG_IMAGE_B64", "fake")
    event = _og_event()
    event["requestContext"]["http"]["method"] = "POST"

    resp = handler.lambda_handler(event, None)
    assert resp["statusCode"] == 405


# ---------------------------------------------------------------------------
# Real asset (if exists on disk)
# ---------------------------------------------------------------------------


def test_real_og_image_is_loaded_at_module_init():
    """If assets/og-image.png exists, it should be loaded at module init.
    If not, _OG_IMAGE_B64 should be empty string (logged as warning).
    """
    if handler._OG_IMAGE_PATH.exists():
        assert handler._OG_IMAGE_B64, "og-image.png exists but failed to load"
        decoded = base64.b64decode(handler._OG_IMAGE_B64)
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        # Asset not generated yet — Step 5 of the implementation plan
        assert handler._OG_IMAGE_B64 == ""
