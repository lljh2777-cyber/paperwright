"""Deterministic, project-authored born-digital PDF fixture generator.

Generated PDFs are temporary test inputs and are deliberately not committed.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    compressed = zlib.compress(data, level=9)
    return (
        b"<< /Length "
        + str(len(compressed)).encode()
        + b" /Filter /FlateDecode "
        + extra
        + b">>\nstream\n"
        + compressed
        + b"\nendstream"
    )


def _image_pixels() -> bytes:
    pixels = bytearray()
    for y in range(12):
        for x in range(16):
            pixels.extend(
                (
                    220 if x < 8 else 30,
                    50 + y * 10,
                    40 if x < 8 else 210,
                )
            )
    return bytes(pixels)


def create_born_digital_fixture(path: Path) -> dict[str, object]:
    page1 = b"\n".join(
        [
            b"BT /F2 22 Tf 60 730 Td (Paper2MD Fixture Title) Tj ET",
            b"BT /F1 11 Tf 60 690 Td (A born-digital paragraph with Caf\\351.) Tj ET",
            b"BT /F2 12 Tf 60 640 Td (Table 1. Honest degradation fixture) Tj ET",
            b"BT /F1 10 Tf 70 615 Td (Group) Tj ET",
            b"BT /F1 10 Tf 220 615 Td (Value) Tj ET",
            b"BT /F1 10 Tf 70 590 Td (Alpha) Tj ET",
            b"BT /F1 10 Tf 220 590 Td (42) Tj ET",
            b"0.8 w 60 575 m 300 575 l S",
            b"60 605 m 300 605 l S",
            b"60 630 m 300 630 l S",
            b"60 575 m 60 630 l S",
            b"200 575 m 200 630 l S",
            b"300 575 m 300 630 l S",
            b"q 120 0 0 90 380 520 cm /Im0 Do Q",
            b"BT /F1 10 Tf 380 505 Td (Embedded image fixture) Tj ET",
        ]
    )
    page2 = b"\n".join(
        [
            b"BT /F2 18 Tf 60 735 Td (Basic Two-Column Layout) Tj ET",
            # Deliberately interleaved native content order.
            b"BT /F1 11 Tf 60 690 Td (LEFT-ONE begins the first column.) Tj ET",
            b"BT /F1 11 Tf 330 690 Td (RIGHT-ONE begins the second column.) Tj ET",
            b"BT /F1 11 Tf 60 660 Td (LEFT-TWO follows left one.) Tj ET",
            b"BT /F1 11 Tf 330 660 Td (RIGHT-TWO follows right one.) Tj ET",
        ]
    )
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> "
            b"/XObject << /Im0 7 0 R >> >> /Contents 8 0 R >>"
        ),
        4: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> "
            b"/Contents 9 0 R >>"
        ),
        5: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        6: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        7: _stream(
            _image_pixels(),
            b"/Type /XObject /Subtype /Image /Width 16 /Height 12 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 ",
        ),
        8: _stream(page1),
        9: _stream(page2),
        10: b"<< /Title (Paper2MD Fixture Title) /Producer (Paper2MD self-test) >>",
    }
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for number in range(1, 11):
        offsets[number] = len(payload)
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(objects[number])
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(b"xref\n0 11\n")
    payload.extend(b"0000000000 65535 f \n")
    for number in range(1, 11):
        payload.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    identifier = hashlib.md5(bytes(payload), usedforsecurity=False).hexdigest()
    payload.extend(
        (
            "trailer\n"
            f"<< /Size 11 /Root 1 0 R /Info 10 0 R /ID [<{identifier}><{identifier}>] >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "pages": 2,
        "rights": "project-authored temporary fixture",
    }
