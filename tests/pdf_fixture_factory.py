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


def create_born_digital_fixture(
    path: Path,
    *,
    include_references: bool = False,
) -> dict[str, object]:
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
    if include_references:
        reference_content = b"\n".join(
            [
                b"BT /F2 18 Tf 60 735 Td (References) Tj ET",
                (
                    b"BT /F1 10 Tf 60 700 Td "
                    b"(1. Smith AB et al. Nature 2020; 10.1234/example.1) Tj ET"
                ),
                (
                    b"BT /F1 10 Tf 60 670 Td "
                    b"(2. Jones CD and Wang E. Science 2021; 12: 30-38.) Tj ET"
                ),
                (
                    b"BT /F1 10 Tf 60 640 Td "
                    b"(3. Lee FG et al. Cell 2022; 8: 100-110.) Tj ET"
                ),
                b"BT /F2 14 Tf 60 590 Td (Acknowledgments) Tj ET",
                (
                    b"BT /F1 10 Tf 60 565 Td "
                    b"(The authors thank the fixture reviewers.) Tj ET"
                ),
                b"BT /F2 14 Tf 60 520 Td (Author Contributions) Tj ET",
                (
                    b"BT /F1 10 Tf 60 495 Td "
                    b"(All authors approved the fixture.) Tj ET"
                ),
                (
                    b"BT /F2 14 Tf 60 450 Td "
                    b"(Supplementary Information) Tj ET"
                ),
                (
                    b"BT /F1 10 Tf 60 425 Td "
                    b"(Supplementary Figure S1 is available online.) Tj ET"
                ),
            ]
        )
        objects[2] = (
            b"<< /Type /Pages /Kids [3 0 R 4 0 R 11 0 R] /Count 3 >>"
        )
        objects[11] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> "
            b"/Contents 12 0 R >>"
        )
        objects[12] = _stream(reference_content)
    maximum_object = max(objects)
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for number in range(1, maximum_object + 1):
        offsets[number] = len(payload)
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(objects[number])
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {maximum_object + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for number in range(1, maximum_object + 1):
        payload.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    identifier = hashlib.md5(bytes(payload), usedforsecurity=False).hexdigest()
    payload.extend(
        (
            "trailer\n"
            f"<< /Size {maximum_object + 1} /Root 1 0 R /Info 10 0 R "
            f"/ID [<{identifier}><{identifier}>] >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "pages": 3 if include_references else 2,
        "rights": "project-authored temporary fixture",
    }


def create_region_render_fixture(
    path: Path,
    *,
    rotation: int = 0,
    blank: bool = False,
) -> dict[str, object]:
    """Create one page with a mixed bitmap/vector Figure and a separate caption."""

    if rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation must be 0/90/180/270")
    content = (
        b""
        if blank
        else b"\n".join(
            [
                b"0 0 0 RG 1 w 50 330 500 400 re S",
                b"q 180 0 0 160 70 500 cm /Im0 Do Q",
                b"0.1 0.3 0.9 RG 2 w 320 500 m 365 570 l 420 520 l 475 650 l 530 550 l S",
                b"0.8 0.1 0.1 rg 330 390 45 70 re f",
                b"0.1 0.7 0.2 rg 400 390 45 110 re f",
                b"BT /F2 15 Tf 65 700 Td (a  Mixed bitmap and vector Figure) Tj ET",
                b"BT /F1 11 Tf 50 300 Td (Figure 1. Caption must remain outside the crop.) Tj ET",
                b"BT /F1 10 Tf 50 270 Td (Unrelated body text below the caption.) Tj ET",
            ]
        )
    )
    rotate = f" /Rotate {rotation}".encode() if rotation else b""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            + rotate
            + b" /Resources << /Font << /F1 4 0 R /F2 5 0 R >> "
            b"/XObject << /Im0 6 0 R >> >> /Contents 7 0 R >>"
        ),
        4: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        5: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        6: _stream(
            _image_pixels(),
            b"/Type /XObject /Subtype /Image /Width 16 /Height 12 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 ",
        ),
        7: _stream(content),
        8: b"<< /Title (Region render fixture) /Producer (Paper2MD self-test) >>",
    }
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for number in range(1, 9):
        offsets[number] = len(payload)
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(objects[number])
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(b"xref\n0 9\n")
    payload.extend(b"0000000000 65535 f \n")
    for number in range(1, 9):
        payload.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    identifier = hashlib.md5(bytes(payload), usedforsecurity=False).hexdigest()
    payload.extend(
        (
            "trailer\n"
            f"<< /Size 9 /Root 1 0 R /Info 8 0 R "
            f"/ID [<{identifier}><{identifier}>] >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "pages": 1,
        "rotation": rotation,
        "blank": blank,
        "rights": "project-authored temporary fixture",
    }


def create_auto_region_fixture(
    path: Path,
    case: str,
    *,
    rotation: int = 0,
) -> dict[str, object]:
    """Create deterministic one-page fixtures for conservative auto planning."""

    if rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation must be 0/90/180/270")
    common_figure = [
        b"0 0 0 RG 1 w 50 330 500 400 re S",
        b"0.2 0.4 0.8 RG 2 w 310 500 m 360 570 l 420 510 l 485 650 l 540 540 l S",
        b"0.8 0.1 0.1 rg 330 390 45 70 re f",
        b"0.1 0.7 0.2 rg 400 390 45 110 re f",
        b"BT /F2 15 Tf 65 700 Td (a  Deterministic Figure panel) Tj ET",
        b"BT /F1 11 Tf 50 300 Td (Figure 1. Explicit same-page caption.) Tj ET",
    ]
    uses_image = case not in {"pure_vector"}
    if case == "single_bitmap":
        content = [
            b"q 300 0 0 220 120 470 cm /Im0 Do Q",
            b"BT /F1 11 Tf 100 430 Td (Figure 1. Complete bitmap caption.) Tj ET",
        ]
    elif case == "pure_vector":
        content = common_figure
    elif case in {"mixed", "rotated"}:
        content = [
            common_figure[0],
            b"q 180 0 0 160 70 500 cm /Im0 Do Q",
            *common_figure[1:],
        ]
    elif case == "multi_panel":
        content = [
            common_figure[0],
            b"q 180 0 0 160 70 500 cm /Im0 Do Q",
            b"q 180 0 0 160 255 500 cm /Im0 Do Q",
            *common_figure[1:],
        ]
    elif case == "adjacent":
        content = [
            b"0 0 0 RG 1 w 35 420 255 290 re S",
            b"35 420 m 290 710 l S",
            b"35 710 m 290 420 l S",
            b"80 450 30 80 re S",
            b"150 450 30 120 re S",
            b"q 180 0 0 160 55 500 cm /Im0 Do Q",
            b"BT /F1 10 Tf 35 390 Td (Figure 1. Left-column caption.) Tj ET",
            b"0 0 0 RG 1 w 322 420 255 290 re S",
            b"322 420 m 577 710 l S",
            b"322 710 m 577 420 l S",
            b"370 450 30 80 re S",
            b"440 450 30 120 re S",
            b"q 180 0 0 160 342 500 cm /Im0 Do Q",
            b"BT /F1 10 Tf 322 390 Td (Figure 2. Right-column caption.) Tj ET",
        ]
    elif case == "continued":
        content = [
            common_figure[0],
            b"q 180 0 0 160 70 500 cm /Im0 Do Q",
            *common_figure[1:],
            b"BT /F2 10 Tf 50 270 Td (Figure 1 continued on next page) Tj ET",
        ]
    elif case == "ambiguous":
        content = [
            common_figure[0],
            b"q 480 0 0 160 60 500 cm /Im0 Do Q",
            *common_figure[1:5],
            b"BT /F1 10 Tf 60 300 Td (Figure 1. Left caption candidate.) Tj ET",
            b"BT /F1 10 Tf 330 300 Td (Figure 2. Right caption candidate.) Tj ET",
        ]
    elif case == "near_full":
        content = [
            b"0 0 0 RG 1 w 10 80 592 690 re S",
            b"10 80 m 602 770 l S",
            b"10 770 m 602 80 l S",
            b"100 200 80 200 re S",
            b"300 200 80 300 re S",
            b"q 240 0 0 220 100 150 cm /Im0 Do Q",
            b"BT /F1 10 Tf 20 50 Td (Figure 1. Near-full-page candidate.) Tj ET",
        ]
    elif case == "body_intrusion":
        content = [
            common_figure[0],
            b"q 180 0 0 160 70 500 cm /Im0 Do Q",
            *common_figure[1:5],
            (
                b"BT /F1 6 Tf 70 470 Td "
                b"(This is deliberately long unrelated body prose inside the candidate "
                b"region and must cause a conservative rejection because it is not a "
                b"short in-figure label.) Tj ET"
            ),
            common_figure[5],
        ]
    elif case == "caption_span_mismatch":
        content = [
            b"0 0 0 RG 1 w 155 350 300 360 re S",
            b"155 350 m 455 710 l S",
            b"155 710 m 455 350 l S",
            b"230 430 30 90 re S",
            b"340 430 30 130 re S",
            b"q 180 0 0 160 210 500 cm /Im0 Do Q",
            (
                b"BT /F1 10 Tf 35 315 Td "
                b"(Figure 1. Wide caption indicates a wider multi-panel figure boundary.) "
                b"Tj ET"
            ),
        ]
    else:
        raise ValueError(f"unknown auto region fixture case: {case}")

    rotate_value = rotation if case == "rotated" else 0
    rotate = f" /Rotate {rotate_value}".encode() if rotate_value else b""
    resources = b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >>"
    if uses_image:
        resources += b" /XObject << /Im0 6 0 R >>"
    resources += b" >>"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            + rotate
            + b" "
            + resources
            + b" /Contents 7 0 R >>"
        ),
        4: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        5: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        ),
        6: (
            _stream(
                _image_pixels(),
                b"/Type /XObject /Subtype /Image /Width 16 /Height 12 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 ",
            )
            if uses_image
            else b"<< /Type /XObject >>"
        ),
        7: _stream(b"\n".join(content)),
        8: (
            b"<< /Title (Auto region fixture) "
            b"/Producer (Paper2MD self-test) >>"
        ),
    }
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for number in range(1, 9):
        offsets[number] = len(payload)
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(objects[number])
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(b"xref\n0 9\n")
    payload.extend(b"0000000000 65535 f \n")
    for number in range(1, 9):
        payload.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    identifier = hashlib.md5(bytes(payload), usedforsecurity=False).hexdigest()
    payload.extend(
        (
            "trailer\n"
            f"<< /Size 9 /Root 1 0 R /Info 8 0 R "
            f"/ID [<{identifier}><{identifier}>] >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "pages": 1,
        "case": case,
        "rotation": rotate_value,
        "rights": "project-authored temporary fixture",
    }
