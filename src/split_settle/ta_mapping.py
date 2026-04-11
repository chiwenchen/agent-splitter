"""TA (target audience) subtitle mapping for landing page personalization.

The Lambda root handler reads ``?ta=<key>`` on ``GET /`` and uses
``resolve_subtitle`` to pick a localized subtitle for the OG meta tags
and the in-app subtitle (the SPA mirrors this table client-side and
keeps it in sync via ``test_ta_mapping_js_parity``).

Unknown ``ta`` values fall back to ``default`` silently — no 404, no
error, no log spam. The whole module is pure data + pure functions:
zero side effects, zero I/O, fast unit tests.
"""
from __future__ import annotations

# Single source of truth for valid ta keys.
# The SPA's i18n must mirror this set (drift-tested in
# tests/test_ta_mapping_js_parity.py).
TA_KEYS: tuple[str, ...] = (
    "default",
    "camping",
    "travel",
    "dining",
    "roommate",
    "family",
    "work",
    "shopping",
)

TA_SUBTITLES: dict[str, dict[str, str]] = {
    "zh-TW": {
        "default":  "與朋友同樂，輕鬆分帳",
        "camping":  "享受露營，輕鬆分帳",
        "travel":   "享受旅行，輕鬆分帳",
        "dining":   "享受美食，輕鬆分帳",
        "roommate": "室友同住，輕鬆分攤",
        "family":   "家庭時光，輕鬆分帳",
        "work":     "同事聚會，輕鬆分帳",
        "shopping": "享受購物，輕鬆分帳",
    },
    "en": {
        "default":  "Hang out with friends, split with ease",
        "camping":  "Enjoy the campsite, split with ease",
        "travel":   "Travel together, split with ease",
        "dining":   "Share the meal, split with ease",
        "roommate": "Share the place, split the rent",
        "family":   "Family time, split with ease",
        "work":     "Team gatherings, split with ease",
        "shopping": "Enjoy the haul, split with ease",
    },
    "ja": {
        "default":  "友だちと楽しく、かんたん割り勘",
        "camping":  "キャンプを楽しんで、かんたん割り勘",
        "travel":   "旅を楽しんで、かんたん割り勘",
        "dining":   "食事を楽しんで、かんたん割り勘",
        "roommate": "ルームシェアでも、かんたん割り勘",
        "family":   "家族の時間、かんたん割り勘",
        "work":     "職場の集まり、かんたん割り勘",
        "shopping": "ショッピングを楽しんで、かんたん割り勘",
    },
}

_DEFAULT_LANG = "zh-TW"


def normalize_ta(raw: str | None) -> str:
    """Normalize an incoming ``ta`` query param.

    Empty / None / unknown / non-allowlist values all collapse to ``"default"``.
    Whitespace is stripped and case is normalized to lower.
    """
    if not raw:
        return "default"
    key = raw.strip().lower()
    return key if key in TA_KEYS else "default"


def resolve_subtitle(ta: str | None, lang: str = _DEFAULT_LANG) -> str:
    """Resolve subtitle text for a given (ta, lang) pair.

    Falls back to the ``zh-TW`` table when ``lang`` is unknown, and to the
    ``"default"`` ta key when ``ta`` is unknown / missing.
    """
    table = TA_SUBTITLES.get(lang, TA_SUBTITLES[_DEFAULT_LANG])
    return table.get(normalize_ta(ta), table["default"])
