"""Small, real image headers for tests: the import path checks the declared
size before storing anything, so fixtures must be parseable images."""
import struct
import zlib


def png(width: int = 1, height: int = 1) -> bytes:
    """A valid PNG declaring width × height (pixel data only for tiny ones)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height)) if width * height <= 4096 else b""
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def jpeg(width: int = 1, height: int = 1, app_padding: int = 0) -> bytes:
    """JPEG markers up to the frame header (enough for the size check)."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16 + app_padding) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" \
        + b"\x00" * app_padding
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def gif(width: int = 1, height: int = 1, frames: int = 1) -> bytes:
    """A GIF with `frames` 1×1 frames on a width × height canvas."""
    frame = b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02\x02\x44\x01\x00"
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + frame * frames + b"\x3b"


def webp(width: int = 1, height: int = 1, frames: int = 0) -> bytes:
    """An extended (VP8X) WebP header, optionally with ANMF frame chunks."""
    vp8x = b"VP8X" + struct.pack("<I", 10) + b"\x02\x00\x00\x00" \
        + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    anmf = b"ANMF" + struct.pack("<I", 16) + b"\x00" * 16
    body = b"WEBP" + vp8x + anmf * frames
    return b"RIFF" + struct.pack("<I", len(body)) + body


def bmp(width: int = 1, height: int = 1) -> bytes:
    return b"BM" + b"\x00" * 12 + struct.pack("<Iii", 40, width, height) + b"\x00" * 28
