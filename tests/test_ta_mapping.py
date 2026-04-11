"""Unit tests for ta_mapping (target audience subtitle resolver)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

from ta_mapping import TA_KEYS, TA_SUBTITLES, normalize_ta, resolve_subtitle


# ---------------------------------------------------------------------------
# normalize_ta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "default"),
        ("", "default"),
        ("   ", "default"),
        ("camping", "camping"),
        ("Camping", "camping"),
        ("CAMPING", "camping"),
        ("  camping  ", "camping"),
        ("travel", "travel"),
        ("dining", "dining"),
        ("roommate", "roommate"),
        ("family", "family"),
        ("work", "work"),
        ("default", "default"),
        ("foo", "default"),
        ("123", "default"),
        ("<script>alert(1)</script>", "default"),
        ("ta=camping", "default"),  # raw value with extra chars
    ],
)
def test_normalize_ta(raw, expected):
    assert normalize_ta(raw) == expected


# ---------------------------------------------------------------------------
# resolve_subtitle
# ---------------------------------------------------------------------------


def test_resolve_subtitle_zh_camping():
    assert resolve_subtitle("camping", "zh-TW") == "享受露營，輕鬆分帳"


def test_resolve_subtitle_en_camping():
    assert resolve_subtitle("camping", "en") == "Enjoy the campsite, split with ease"


def test_resolve_subtitle_ja_camping():
    assert resolve_subtitle("camping", "ja") == "キャンプを楽しんで、かんたん割り勘"


def test_resolve_subtitle_zh_default_when_no_ta():
    assert resolve_subtitle(None, "zh-TW") == "與朋友同樂，輕鬆分帳"


def test_resolve_subtitle_zh_default_when_unknown_ta():
    assert resolve_subtitle("unknown", "zh-TW") == "與朋友同樂，輕鬆分帳"


def test_resolve_subtitle_falls_back_to_zh_when_unknown_lang():
    # Unknown language → fall back to zh-TW table, default key
    assert resolve_subtitle("camping", "fr") == "享受露營，輕鬆分帳"


def test_resolve_subtitle_handles_case_insensitive_ta():
    assert resolve_subtitle("CAMPING", "zh-TW") == "享受露營，輕鬆分帳"


# ---------------------------------------------------------------------------
# TA_SUBTITLES table integrity
# ---------------------------------------------------------------------------


def test_ta_keys_set_is_complete():
    expected = {"default", "camping", "travel", "dining", "roommate", "family", "work"}
    assert set(TA_KEYS) == expected


def test_all_languages_have_all_ta_keys():
    expected_keys = set(TA_KEYS)
    for lang, table in TA_SUBTITLES.items():
        assert set(table.keys()) == expected_keys, (
            f"Language {lang!r} has keys {set(table.keys())}, expected {expected_keys}"
        )


def test_no_empty_subtitle_strings():
    for lang, table in TA_SUBTITLES.items():
        for ta_key, text in table.items():
            assert text.strip(), f"Empty subtitle: lang={lang!r}, ta={ta_key!r}"


def test_subtitles_dict_has_three_languages():
    assert set(TA_SUBTITLES.keys()) == {"zh-TW", "en", "ja"}


def test_resolve_subtitle_returns_string_for_every_combination():
    """Sanity: every (lang, ta_key) combination yields a non-empty string."""
    for lang in TA_SUBTITLES:
        for ta_key in TA_KEYS:
            result = resolve_subtitle(ta_key, lang)
            assert isinstance(result, str)
            assert result.strip()
