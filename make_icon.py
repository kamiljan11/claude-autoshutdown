"""Generuje assets/icon.ico bez zadnych zaleznosci (PNG zapakowany w kontener ICO).

Ikona: symbol zasilania (okrag z przerwa + pionowa kreska) na ciemnym tle,
w kolorach programu. Uruchom: python make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 64, 128, 256)
BG = (0x14, 0x18, 0x1D)
RING = (0x3F, 0xB9, 0x50)
OUT = Path(__file__).resolve().parent / "assets" / "icon.ico"


def _render(size: int) -> bytes:
    """RGBA, wiersz po wierszu. Antyaliasing przez 3x3 supersampling."""
    center = size / 2
    radius = size * 0.34
    stroke = max(1.5, size * 0.11)
    gap = math.radians(38)  # przerwa u gory, symetryczna wzgledem 12:00
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            hits = 0
            for sy in (0.17, 0.5, 0.83):
                for sx in (0.17, 0.5, 0.83):
                    px, py = x + sx - center, y + sy - center
                    dist = math.hypot(px, py)
                    angle = math.atan2(py, px)  # 0 = prawo, -pi/2 = gora
                    on_ring = (abs(dist - radius) <= stroke / 2
                               and not (-math.pi / 2 - gap < angle < -math.pi / 2 + gap))
                    on_bar = (abs(px) <= stroke / 2
                              and -radius - stroke * 0.2 <= py <= -radius * 0.05)
                    if on_ring or on_bar:
                        hits += 1
            alpha = hits / 9
            r, g, b = (round(BG[i] + (RING[i] - BG[i]) * alpha) for i in range(3))
            # tlo: zaokraglony kwadrat (promien 20 %), reszta przezroczysta
            corner = size * 0.2
            cx = min(x, size - 1 - x)
            cy = min(y, size - 1 - y)
            inside = (cx >= corner or cy >= corner
                      or math.hypot(corner - cx, corner - cy) <= corner)
            pixels += bytes((r, g, b, 255 if inside else 0))
    return bytes(pixels)


def _png(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def build_ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    images = [(s, _png(s, _render(s))) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(png), offset + len(blobs))
        blobs += png
    return header + entries + blobs


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(build_ico())
    print(f"{OUT} ({OUT.stat().st_size:,} B, rozmiary {SIZES})")


if __name__ == "__main__":
    main()
