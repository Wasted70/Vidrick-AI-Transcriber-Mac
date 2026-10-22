"""Small, in-memory rounded button surfaces. Standard library only; no I/O."""

import base64
import math
import struct
import zlib


def rounded_surface(fill, edge, *, width=48, height=28, radius=7):
    """Return a PNG for a ttk nine-slice border with antialiased corners."""
    colors = [tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) for color in (fill, edge)]
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)  # PNG scanline filter: none
        for x in range(width):
            px, py = x + 0.5, y + 0.5
            cx = min(max(px, radius), width - radius)
            cy = min(max(py, radius), height - radius)
            distance = math.hypot(px - cx, py - cy)
            coverage = min(1.0, max(0.0, radius + 0.5 - distance))
            border_mix = min(1.0, max(0.0, distance - radius + 1.5))
            # A restrained highlight makes the face feel softly raised.
            highlight = 3 * (1 - y / max(1, height-1))
            rgb = [round(min(255, colors[0][i] * (1-border_mix) +
                             colors[1][i] * border_mix + highlight * (1-border_mix)))
                   for i in range(3)]
            pixels.extend((*rgb, round(255 * coverage)))

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(pixels)))
    png += chunk(b'IEND', b'')
    return base64.b64encode(png).decode('ascii')
