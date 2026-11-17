"""Generate the PWA icons (stdlib only): `uv run python dev/make_icons.py`.

Two overlapping circles on a solid square. Artwork stays inside the central
60% so the same PNGs work as maskable icons.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "pairtner" / "web" / "icons"
BG = (43, 92, 255)
FG = (255, 255, 255)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#2b5cff"/>
<circle cx="214" cy="256" r="84" fill="none" stroke="#fff" stroke-width="22"/>
<circle cx="298" cy="256" r="84" fill="none" stroke="#fff" stroke-width="22"/>
</svg>
"""


def _png(size: int) -> bytes:
    scale = size / 512
    circles = [(214 * scale, 256 * scale), (298 * scale, 256 * scale)]
    radius, half = 84 * scale, 11 * scale
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            on = any(abs(((x + .5 - cx) ** 2 + (y + .5 - cy) ** 2) ** .5 - radius) <= half
                     for cx, cy in circles)
            rows.extend(FG if on else BG)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        (OUT / f"icon-{size}.png").write_bytes(_png(size))
    (OUT / "icon.svg").write_text(SVG, encoding="utf-8")


if __name__ == "__main__":
    main()
