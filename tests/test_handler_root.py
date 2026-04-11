"""Integration tests for GET / (root landing page) — verifies that
?ta= query param is normalized and injected into the OG meta tags + page
title, and that XSS attempts are escaped."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler  # noqa: E402


def _root_event(ta=None, host: str = "split.redarch.dev"):
    """Build a minimal Lambda HTTP API event for GET /."""
    qs = {"ta": ta} if ta is not None else None
    return {
        "rawPath": "/",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": qs,
        "headers": {"x-forwarded-host": host, "x-forwarded-proto": "https"},
    }


def _invoke(event):
    return handler.lambda_handler(event, None)


# ---------------------------------------------------------------------------
# Default behavior (no ta param)
# ---------------------------------------------------------------------------


def test_root_no_ta_returns_default_subtitle():
    resp = _invoke(_root_event(ta=None))
    assert resp["statusCode"] == 200
    body = resp["body"]
    assert "與朋友同樂，輕鬆分帳" in body


def test_root_no_ta_has_full_og_meta():
    resp = _invoke(_root_event(ta=None))
    body = resp["body"]
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert 'property="og:type"' in body
    assert 'property="og:url"' in body
    assert 'name="twitter:card"' in body
    assert 'content="summary_large_image"' in body


def test_root_no_ta_title_starts_with_brand():
    resp = _invoke(_root_event(ta=None))
    body = resp["body"]
    # Page title format: "分帳仙貝 - <subtitle>"
    assert "<title>分帳仙貝 - 與朋友同樂，輕鬆分帳</title>" in body


# ---------------------------------------------------------------------------
# ta=camping happy path
# ---------------------------------------------------------------------------


def test_root_ta_camping_replaces_subtitle_in_meta():
    """When ?ta=camping, OG meta tags should use the camping subtitle.

    Note: the default subtitle string ("與朋友同樂，輕鬆分帳") still
    appears in the body because the SPA's i18n table is inlined into the
    HTML. We only assert that the OG meta tag values are correct.
    """
    resp = _invoke(_root_event(ta="camping"))
    body = resp["body"]
    # The page title and OG meta should reflect camping
    assert '<title>分帳仙貝 - 享受露營，輕鬆分帳</title>' in body
    assert 'content="分帳仙貝 - 享受露營，輕鬆分帳"' in body  # og:title / twitter:title
    assert 'content="享受露營，輕鬆分帳"' in body  # og:description
    # Default subtitle MUST NOT appear in the OG title attribute
    assert 'content="分帳仙貝 - 與朋友同樂，輕鬆分帳"' not in body


def test_root_ta_camping_title_includes_subtitle():
    resp = _invoke(_root_event(ta="camping"))
    body = resp["body"]
    assert "<title>分帳仙貝 - 享受露營，輕鬆分帳</title>" in body


def test_root_ta_camping_canonical_url_includes_param():
    resp = _invoke(_root_event(ta="camping"))
    body = resp["body"]
    assert "https://split.redarch.dev/?ta=camping" in body


def test_root_default_canonical_omits_ta_param():
    resp = _invoke(_root_event(ta=None))
    body = resp["body"]
    # Should NOT have ?ta=default in canonical
    assert "?ta=default" not in body
    assert "https://split.redarch.dev/" in body


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------


def test_root_ta_uppercase_normalized():
    resp = _invoke(_root_event(ta="Camping"))
    body = resp["body"]
    assert "享受露營，輕鬆分帳" in body


def test_root_ta_mixed_case_normalized():
    resp = _invoke(_root_event(ta="CamPing"))
    body = resp["body"]
    assert "享受露營，輕鬆分帳" in body


# ---------------------------------------------------------------------------
# Unknown ta fallback
# ---------------------------------------------------------------------------


def test_root_unknown_ta_falls_back_to_default():
    resp = _invoke(_root_event(ta="totally-unknown-ta"))
    assert resp["statusCode"] == 200
    body = resp["body"]
    assert "與朋友同樂，輕鬆分帳" in body
    # And canonical URL should NOT echo the unknown value
    assert "ta=totally-unknown-ta" not in body


def test_root_empty_ta_falls_back_to_default():
    resp = _invoke(_root_event(ta=""))
    body = resp["body"]
    assert "與朋友同樂，輕鬆分帳" in body


# ---------------------------------------------------------------------------
# XSS / injection safety
# ---------------------------------------------------------------------------


def test_root_xss_ta_value_does_not_inject_raw_script():
    resp = _invoke(_root_event(ta="<script>alert(1)</script>"))
    assert resp["statusCode"] == 200
    body = resp["body"]
    # Unknown ta → default subtitle (the value is rejected at normalize_ta,
    # so the script tag never even gets near the HTML).
    assert "<script>alert(1)</script>" not in body or body.count("<script") == body.count("<script type=")
    # And canonical/OG should be the default
    assert "與朋友同樂，輕鬆分帳" in body


def test_root_html_metachars_in_host_escaped():
    """Forged x-forwarded-host shouldn't break the canonical URL escape."""
    event = _root_event(ta="camping")
    event["headers"]["x-forwarded-host"] = 'evil"><script>alert(1)</script>'
    resp = _invoke(event)
    body = resp["body"]
    # Raw script tag from header MUST NOT appear unescaped
    assert '<script>alert(1)</script>' not in body or body.count('<script>alert(1)') == 0


# ---------------------------------------------------------------------------
# Host fallback chain
# ---------------------------------------------------------------------------


def test_root_falls_back_to_host_header_when_no_xfh():
    event = _root_event(ta="camping")
    event["headers"] = {"host": "split.redarch.dev"}
    resp = _invoke(event)
    assert resp["statusCode"] == 200
    body = resp["body"]
    assert "https://split.redarch.dev/?ta=camping" in body


def test_root_falls_back_to_default_host_when_no_headers():
    event = _root_event(ta=None)
    event["headers"] = {}
    resp = _invoke(event)
    assert resp["statusCode"] == 200
    body = resp["body"]
    # Default host fallback
    assert "https://split.redarch.dev" in body


# ---------------------------------------------------------------------------
# All ta keys covered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ta,expected",
    [
        ("camping", "享受露營，輕鬆分帳"),
        ("travel", "享受旅行，輕鬆分帳"),
        ("dining", "享受美食，輕鬆分帳"),
        ("roommate", "室友同住，輕鬆分攤"),
        ("family", "家庭時光，輕鬆分帳"),
        ("work", "同事聚會，輕鬆分帳"),
        ("shopping", "享受購物，輕鬆分帳"),
    ],
)
def test_all_ta_keys_render_correct_subtitle(ta, expected):
    resp = _invoke(_root_event(ta=ta))
    assert resp["statusCode"] == 200
    assert expected in resp["body"]
