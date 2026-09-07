#!/usr/bin/env python3
"""
Draw Mercury's app icon.

Committed as a script rather than as a one-off, because an icon is a thing
you tweak: the mark is defined here in a few numbers, and regenerating it is
`python3 scripts/make_app_icon.py`. A PNG in the repository with no way to
reproduce it is a file nobody dares change.

**The mark.** A sun on a horizon, in the day's accent on paper. Mercury is a
MORNING briefing, and the reason to draw a sunrise rather than a waveform or
a microphone is that every audio app is already a waveform — the thing that
distinguishes this one is the time of day it belongs to.

The horizon is a doubled rule: a heavy line with a hairline under it, which
is the masthead rule from ADR-012 and the same device the app's own Home
screen uses. It is the one detail that ties the icon to the product rather
than to the category.

**Why hand-rolled PNG.** No Pillow on this machine and no rasteriser for
SVG, and adding either as a dependency to draw one file would be the larger
change. PNG is a container around zlib-compressed scanlines; writing it is
forty lines and has no dependency beyond the standard library.

Antialiasing is 4x supersampling — flat colours and one circle, so there is
nothing subtler to be gained from a real rasteriser.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 1024
SUPERSAMPLE = 4

# ADR-012's palette. Paper rather than white, and the accent that appears on
# the masthead rule and the play glyph.
PAPER = (0xFB, 0xFA, 0xF8)
ACCENT = (0x8E, 0x3F, 0x51)

# Geometry, as fractions of the canvas so the numbers survive a size change.
# The first cut of these made a bowler hat: a rule that stopped well short
# of the edges reads as a BRIM, not a horizon, and the dome sitting on it
# completed the illusion. A horizon has to run to the edge of what you can
# see, so the inset is now small enough to read as margin rather than as
# the end of the line.
HORIZON_Y = 0.585         # where the sun sits — lifted, to centre the mass
SUN_RADIUS = 0.235
RULE_WEIGHT = 0.030       # the heavy masthead rule
HAIRLINE_WEIGHT = 0.009
HAIRLINE_GAP = 0.026
RULE_INSET = 0.055        # margin, not a brim


def _blend(under: tuple[int, int, int], over: tuple[int, int, int], a: float):
    return tuple(round(u + (o - u) * a) for u, o in zip(under, over, strict=True))


def _coverage(x: int, y: int, n: int) -> float:
    """How much of pixel (x, y) the mark covers, by supersampling.

    A circle and two rectangles: exact analytic coverage would be more
    accurate and considerably more code, and at 1024px the difference is
    invisible.
    """
    hits = 0
    for sy in range(SUPERSAMPLE):
        for sx in range(SUPERSAMPLE):
            u = (x + (sx + 0.5) / SUPERSAMPLE) / n
            v = (y + (sy + 0.5) / SUPERSAMPLE) / n
            if _inside(u, v):
                hits += 1
    return hits / (SUPERSAMPLE * SUPERSAMPLE)


def _inside(u: float, v: float) -> bool:
    """Is this point part of the mark? `u`/`v` are 0-1 across the canvas."""
    # The sun: a disc, clipped to the sky. A half-disc sitting ON the horizon
    # rather than a full circle floating above it — the horizon is what makes
    # it a sunrise instead of a dot.
    dx = u - 0.5
    dy = v - HORIZON_Y
    if dy <= 0 and (dx * dx + dy * dy) <= SUN_RADIUS * SUN_RADIUS:
        return True

    if RULE_INSET <= u <= 1 - RULE_INSET:
        # The heavy rule, its top edge exactly on the horizon so the sun
        # rests on it rather than overlapping it.
        if HORIZON_Y <= v <= HORIZON_Y + RULE_WEIGHT:
            return True
        # The hairline under it. Two weights with air between them is the
        # masthead device; a single rule would just be a line.
        top = HORIZON_Y + RULE_WEIGHT + HAIRLINE_GAP
        if top <= v <= top + HAIRLINE_WEIGHT:
            return True

    return False


def render(size: int = SIZE) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # PNG filter type 0 (None) for this scanline
        for x in range(size):
            a = _coverage(x, y, size)
            row.extend(PAPER if a == 0 else _blend(PAPER, ACCENT, a))
        rows.append(bytes(row))
    return b"".join(rows)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int = SIZE) -> None:
    raw = render(size)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = (
        root / "ios" / "Mercury" / "Assets.xcassets" / "AppIcon.appiconset"
    )
    out.mkdir(parents=True, exist_ok=True)
    target = out / "icon-1024.png"
    write_png(target)
    print(f"wrote {target} ({target.stat().st_size:,} bytes)")
