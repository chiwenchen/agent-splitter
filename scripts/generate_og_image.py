#!/usr/bin/env python3
"""Generate the OG share card for 分帳仙貝.

Output: assets/og-image.png (1200x630, ~30-60 KB)

Run once locally:
    python3 -m pip install --user Pillow
    python3 scripts/generate_og_image.py

Commit the resulting PNG to git so the SAM build doesn't need Pillow at
deploy time. The Lambda Makefile build hook copies assets/og-image.png
into the build artifact, where handler.py reads it once at module init.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---- Brand palette (matches handler.py CSS variables) ----
BG = (45, 74, 74)        # --layer-1 #2d4a4a
ACCENT = (232, 168, 76)  # --accent  #e8a84c
TEXT = (224, 213, 196)   # --text-on-dark #e0d5c4
MUTED = (138, 170, 158)  # --text-muted #8aaa9e

WIDTH, HEIGHT = 1200, 630

# PingFang.ttc face index layout (verified by ImageFont.getname()):
#   0=HK Reg  1=MO Reg  2=TC Reg  3=SC Reg
#   4=HK Med  5=MO Med  6=TC Med  7=SC Med
#   8=HK Semi 9=MO Semi 10=TC Semi 11=SC Semi
#   12=HK Light ... 16=HK Thin ... 20=HK Ultra
PINGFANG_TC_SEMIBOLD = 10
PINGFANG_TC_MEDIUM = 6
PINGFANG_TC_REGULAR = 2

# Path candidates: macOS 14+ relocated fonts to AssetsV2 with hashed paths.
# We probe both the modern hashed location and the legacy stable path.
PINGFANG_PATHS = [
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _resolve_pingfang() -> str:
    """Find PingFang.ttc on this system."""
    for p in PINGFANG_PATHS:
        if Path(p).exists():
            return p
    # Fall back: any PingFang.ttc anywhere in /System/Library
    candidates = list(Path("/System/Library").rglob("PingFang.ttc"))
    if candidates:
        return str(candidates[0])
    raise RuntimeError(
        "PingFang.ttc not found. Run on macOS where it ships with the OS."
    )


def _load(font_path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size, index=index)


def _center_text(draw: ImageDraw.ImageDraw, text: str, font, y: int, fill) -> int:
    """Draw text centered horizontally; return rendered height."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) // 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def main() -> None:
    pingfang = _resolve_pingfang()
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # 2px gold inset frame for premium feel
    pad = 24
    draw.rectangle([pad, pad, WIDTH - pad, HEIGHT - pad], outline=ACCENT, width=2)

    # Brand title — PingFang TC Semibold (heaviest weight in PingFang.ttc)
    title_font = _load(pingfang, PINGFANG_TC_SEMIBOLD, 170)
    title_y = 170
    title_h = _center_text(draw, "分帳仙貝", title_font, y=title_y, fill=ACCENT)

    # Gold underline bar
    bar_y = title_y + title_h + 36
    bar_w, bar_h = 240, 6
    draw.rectangle(
        [(WIDTH - bar_w) // 2, bar_y, (WIDTH + bar_w) // 2, bar_y + bar_h],
        fill=ACCENT,
    )

    # Tagline — PingFang TC Medium
    tag_font = _load(pingfang, PINGFANG_TC_MEDIUM, 48)
    _center_text(draw, "三步驟完成分帳", tag_font, y=bar_y + bar_h + 36, fill=TEXT)

    # URL footer — PingFang TC Regular
    url_font = _load(pingfang, PINGFANG_TC_REGULAR, 28)
    _center_text(draw, "split.redarch.dev", url_font, y=HEIGHT - 80, fill=MUTED)

    # Asset lives next to handler.py so SAM packages it automatically
    # without requiring a custom Makefile build hook (CodeUri pulls in
    # the whole src/split_settle/ directory).
    out = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "split_settle"
        / "assets"
        / "og-image.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
