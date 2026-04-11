"""Cross-language drift test: ensure src/split_settle/app.js mirrors
src/split_settle/ta_mapping.py exactly.

Both ends maintain a copy of the ta-key set + the per-language subtitles.
We don't have a JS test framework wired up, so this test parses app.js
with regex from Python and asserts the keys + values match.

If you change one side, change the other; this test will fail loudly
otherwise.
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

from ta_mapping import TA_KEYS, TA_SUBTITLES  # noqa: E402

APP_JS = Path(__file__).resolve().parent.parent / "src" / "split_settle" / "app.js"


@pytest.fixture(scope="module")
def app_js_text() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# TA_KEYS parity
# ---------------------------------------------------------------------------


def test_app_js_declares_ta_keys(app_js_text):
    """app.js must declare a TA_KEYS array containing exactly the same set
    of keys as ta_mapping.TA_KEYS."""
    match = re.search(r"const\s+TA_KEYS\s*=\s*\[([^\]]+)\]", app_js_text)
    assert match, "TA_KEYS not found in app.js"
    raw_items = match.group(1)
    js_keys = set(re.findall(r"['\"]([a-z_]+)['\"]", raw_items))
    assert js_keys == set(TA_KEYS), (
        f"app.js TA_KEYS {js_keys} differs from Python TA_KEYS {set(TA_KEYS)}"
    )


# ---------------------------------------------------------------------------
# taSubtitles parity
# ---------------------------------------------------------------------------


def _extract_lang_block(text: str, lang: str) -> str:
    """Extract the body of a single language entry from app.js taSubtitles."""
    # Match e.g.  en: { ... },  or  'zh-TW': { ... },
    quoted = f"['\"]{re.escape(lang)}['\"]"
    pattern = rf"(?:{quoted}|{re.escape(lang)})\s*:\s*\{{([^}}]+)\}}"
    m = re.search(pattern, text)
    assert m, f"language {lang!r} not found in app.js taSubtitles"
    return m.group(1)


def _parse_lang_keys(block: str) -> set:
    """Parse {key: 'value'} entries inside a single-language block."""
    return set(re.findall(r"([a-z_]+)\s*:\s*['\"]", block))


def test_app_js_has_all_three_languages(app_js_text):
    # Find taSubtitles object opening
    assert "const taSubtitles" in app_js_text, "taSubtitles not declared in app.js"
    for lang in ("en", "zh-TW", "ja"):
        _extract_lang_block(app_js_text, lang)


def test_app_js_taSubtitles_keys_match_python(app_js_text):
    expected_keys = set(TA_KEYS)
    for lang in TA_SUBTITLES:
        block = _extract_lang_block(app_js_text, lang)
        js_keys = _parse_lang_keys(block)
        assert js_keys == expected_keys, (
            f"app.js taSubtitles[{lang!r}] keys {js_keys} differ from {expected_keys}"
        )


def test_app_js_taSubtitles_values_match_python(app_js_text):
    """Strong drift check: each (lang, ta) value in app.js must equal the
    corresponding value in ta_mapping.TA_SUBTITLES."""
    for lang, table in TA_SUBTITLES.items():
        block = _extract_lang_block(app_js_text, lang)
        for ta_key, expected_value in table.items():
            # Match `ta_key: '<value>'` allowing single or double quotes
            pattern = rf"{ta_key}\s*:\s*['\"]([^'\"]+)['\"]"
            m = re.search(pattern, block)
            assert m, f"{lang}.{ta_key} missing in app.js"
            actual = m.group(1)
            assert actual == expected_value, (
                f"{lang}.{ta_key} drift: app.js={actual!r}, py={expected_value!r}"
            )
