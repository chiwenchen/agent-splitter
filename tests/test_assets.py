"""Sanity tests for static assets shipped with the Lambda.

These tests are zero-dependency: they parse the PNG IHDR chunk by hand
with `struct`, so pytest doesn't need Pillow installed.
"""
import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OG_IMAGE = REPO_ROOT / "src" / "split_settle" / "assets" / "og-image.png"


def _read_png_header(path: Path) -> tuple:
    """Parse a PNG IHDR chunk and return (width, height, bit_depth, color_type).

    Layout (per PNG spec, RFC 2083):
        bytes  0..7    : signature (89 50 4E 47 0D 0A 1A 0A)
        bytes  8..11   : IHDR length (always 13)
        bytes 12..15   : 'IHDR' chunk type
        bytes 16..19   : width  (uint32 big-endian)
        bytes 20..23   : height (uint32 big-endian)
        byte  24       : bit depth
        byte  25       : color type
    """
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path} missing IHDR"
    width, height = struct.unpack(">II", data[16:24])
    bit_depth = data[24]
    color_type = data[25]
    return width, height, bit_depth, color_type


def test_og_image_exists():
    assert OG_IMAGE.exists(), (
        f"{OG_IMAGE} not found. Run: python3 scripts/generate_og_image.py"
    )


def test_og_image_is_valid_png():
    width, height, bit_depth, color_type = _read_png_header(OG_IMAGE)
    assert width > 0
    assert height > 0
    assert bit_depth in (1, 2, 4, 8, 16)


def test_og_image_dimensions_are_1200x630():
    """Facebook OG / LINE / iMessage standard share card size."""
    width, height, _, _ = _read_png_header(OG_IMAGE)
    assert (width, height) == (1200, 630), (
        f"og-image.png is {width}x{height}, expected 1200x630. "
        "Re-run scripts/generate_og_image.py."
    )


def test_og_image_size_is_reasonable():
    """OG card should be small enough to deploy in Lambda but large enough
    to look like an actual image (not a 1px placeholder)."""
    size = OG_IMAGE.stat().st_size
    assert 5_000 < size < 500_000, (
        f"og-image.png is {size} bytes — outside expected 5KB-500KB range"
    )
