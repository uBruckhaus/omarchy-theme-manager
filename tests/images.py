"""Small, real image headers for tests: the import path checks the declared
size before storing anything, so fixtures must be parseable images."""
import struct
import zlib


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width: int = 1, height: int = 1, extra: bytes = b"") -> bytes:
    """A valid PNG declaring width × height (pixel data only for tiny ones);
    `extra` (ready-made chunks) goes between IHDR and IDAT."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height)) if width * height <= 4096 else b""
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + extra \
        + png_chunk(b"IDAT", zlib.compress(rows)) + png_chunk(b"IEND", b"")


def ztxt(size: int, keyword: bytes = b"Comment") -> bytes:
    """A zTXt chunk whose text inflates to `size` bytes."""
    return png_chunk(b"zTXt", keyword + b"\x00\x00" + zlib.compress(b"A" * size, 9))


def itxt(text: bytes, compressed: bool = False, keyword: bytes = b"XML:com.adobe.xmp") -> bytes:
    body = zlib.compress(text, 9) if compressed else text
    return png_chunk(b"iTXt", keyword + b"\x00" + (b"\x01\x00" if compressed else b"\x00\x00")
                     + b"\x00\x00" + body)


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


def _box(kind: bytes, payload: bytes, full: bool = False, version: int = 0, flags: int = 0) -> bytes:
    if full:
        payload = bytes([version]) + flags.to_bytes(3, "big") + payload
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def heif(width: int = 64, height: int = 64, grid: tuple[int, int] | None = None,
         item_type: bytes = b"hvc1", ispe: bool = True) -> bytes:
    """A HEIF container (no coded pixels) with one image item of `item_type`
    whose 'ispe' says width × height; with `grid`, the item is a grid whose
    output canvas (stored in 'idat') is grid[0] × grid[1]."""
    if grid is not None:
        item_type = b"grid"
    ftyp = _box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic")
    hdlr = _box(b"hdlr", b"\x00" * 4 + b"pict" + b"\x00" * 13, full=True)
    pitm = _box(b"pitm", struct.pack(">H", 1), full=True)
    infe = _box(b"infe", struct.pack(">HH", 1, 0) + item_type + b"\x00", full=True, version=2)
    iinf = _box(b"iinf", struct.pack(">H", 1) + infe, full=True)
    grid_data = b""
    if grid is not None:
        big = grid[0] > 0xFFFF or grid[1] > 0xFFFF
        grid_data = bytes([0, 1 if big else 0, 0, 0]) + (struct.pack(">II", *grid) if big
                                                         else struct.pack(">HH", *grid))
    # iloc v1: offset 4 bytes, length 4 bytes, base 0, index 0; construction method 1 (idat).
    iloc = _box(b"iloc", bytes([0x44, 0x00]) + struct.pack(">H", 1)
                + struct.pack(">HHHH", 1, 1, 0, 1) + struct.pack(">II", 0, len(grid_data)),
                full=True, version=1)
    props = _box(b"ispe", struct.pack(">II", width, height), full=True) if ispe else b""
    ipco = _box(b"ipco", props)
    ipma = _box(b"ipma", struct.pack(">IHB", 1, 1, 1) + b"\x81", full=True)
    iprp = _box(b"iprp", ipco + ipma)
    idat = _box(b"idat", grid_data)
    meta = _box(b"meta", hdlr + pitm + iinf + iloc + iprp + idat, full=True)
    return ftyp + meta + _box(b"mdat", b"\x00" * 16)
