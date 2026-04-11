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

# Font discovery (CJK-capable, fallback chain)
FONT_CANDIDATES_BOLD = [
    ("/System/Library/Fonts/PingFang.ttc", 4),  # macOS Heavy
    ("/System/Library/Fonts/PingFang.ttc", 3),  # macOS Semibold
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
]
FONT_CANDIDATES_REG = [
    ("/System/Library/Fonts/PingFang.ttc", 1),  # macOS Regular
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
]


def _load(size: int, candidates: list) -> ImageFont.FreeTypeFont:
    """Load the first available CJK-capable font at the given size."""
    for path, idx in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue
    raise RuntimeError(
        "No CJK-capable font found. Install fonts-noto-cjk on Linux "
        "or run on macOS where PingFang.ttc ships with the OS."
    )


def _center_text(draw: ImageDraw.ImageDraw, text: str, font, y: int, fill) -> int:
    """Draw text centered horizontally; return rendered height."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) // 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # 2px gold inset frame for premium feel
    pad = 24
    draw.rectangle([pad, pad, WIDTH - pad, HEIGHT - pad], outline=ACCENT, width=2)

    # Brand title
    title_font = _load(170, FONT_CANDIDATES_BOLD)
    title_y = 170
    title_h = _center_text(draw, "分帳仙貝", title_font, y=title_y, fill=ACCENT)

    # Gold underline bar
    bar_y = title_y + title_h + 36
    bar_w, bar_h = 240, 6
    draw.rectangle(
        [(WIDTH - bar_w) // 2, bar_y, (WIDTH + bar_w) // 2, bar_y + bar_h],
        fill=ACCENT,
    )

    # Tagline
    tag_font = _load(48, FONT_CANDIDATES_REG)
    _center_text(draw, "三步驟完成分帳", tag_font, y=bar_y + bar_h + 36, fill=TEXT)

    # URL footer
    url_font = _load(28, FONT_CANDIDATES_REG)
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
