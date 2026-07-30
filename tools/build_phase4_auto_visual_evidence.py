#!/usr/bin/env python3
"""Build a small, non-Git visual review bundle from existing Phase 4 outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_page(pdf_path: Path, page_number: int, scale: float) -> Image.Image:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page = document[page_number - 1]
        try:
            return page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()


def _annotate(image: Image.Image, bbox: dict[str, float], scale: float) -> Image.Image:
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    x0 = round(bbox["x"] * scale)
    y0 = round(bbox["y"] * scale)
    x1 = round((bbox["x"] + bbox["width"]) * scale)
    y1 = round((bbox["y"] + bbox["height"]) * scale)
    for offset in range(4):
        draw.rectangle(
            (x0 - offset, y0 - offset, x1 + offset, y1 + offset),
            outline=(220, 0, 0),
        )
    return marked


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
    return canvas


def _contact(
    panels: list[tuple[str, Image.Image]],
    output: Path,
    *,
    panel_width: int = 640,
    panel_height: int = 900,
) -> None:
    header = 34
    canvas = Image.new(
        "RGB", (panel_width * len(panels), panel_height + header), "white"
    )
    draw = ImageDraw.Draw(canvas)
    for index, (label, image) in enumerate(panels):
        x = index * panel_width
        draw.text((x + 8, 9), label, fill="black")
        canvas.paste(_fit(image, panel_width, panel_height), (x, header))
    canvas.save(output, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--auto-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("visual evidence output already exists")
    args.output.mkdir(parents=True)

    source_records = {
        item["id"]: item
        for item in json.loads(
            (args.repo / "realworld/oa_sources.json").read_text(encoding="utf-8")
        )["papers"]
    }
    targets = [
        ("RW2-005", 3, "rendered"),
        ("RW2-007", 5, "rendered"),
        ("RW2-005", 7, "rejected"),
    ]
    scale = 1.25
    records = []
    for paper_id, page_number, expected in targets:
        pdf_path = args.pdf_dir / f"{paper_id}.pdf"
        source = source_records[paper_id]
        if (
            pdf_path.stat().st_size != source["size_bytes"]
            or sha256(pdf_path) != source["sha256"]
        ):
            raise RuntimeError(f"{paper_id} source identity mismatch")
        manifest_path = args.auto_root / paper_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        page_image = _render_page(pdf_path, page_number, scale)
        prefix = f"{paper_id.lower()}-p{page_number:03d}"
        page_path = args.output / f"{prefix}-page.png"
        page_image.save(page_path, format="PNG", optimize=True)

        if expected == "rendered":
            figure = next(
                item
                for item in manifest["figures"]
                if item["page"] == page_number
                and item["region_render"]["status"] == "rendered"
            )
            marked = _annotate(page_image, figure["bbox"], scale)
            marked_path = args.output / f"{prefix}-bbox.png"
            marked.save(marked_path, format="PNG", optimize=True)
            native_source = args.auto_root / paper_id / figure["native_asset"]["path"]
            region_source = args.auto_root / paper_id / figure["asset"]["path"]
            native_path = args.output / f"{prefix}-native.png"
            region_path = args.output / f"{prefix}-region.png"
            shutil.copyfile(native_source, native_path)
            shutil.copyfile(region_source, region_path)
            with Image.open(native_path) as native_image, Image.open(
                region_path
            ) as region_image:
                contact_path = args.output / f"{prefix}-contact.png"
                _contact(
                    [
                        ("PAGE + APPROVED BBOX", marked),
                        ("PHASE 3 NATIVE ASSET", native_image.convert("RGB")),
                        ("AUTO REGION ASSET", region_image.convert("RGB")),
                    ],
                    contact_path,
                )
            record = {
                "paper_id": paper_id,
                "page": page_number,
                "status": "rendered",
                "figure_id": figure["figure_id"],
                "bbox": figure["bbox"],
                "page_area_ratio": figure["region_render"]["page_area_ratio"],
                "dpi": figure["region_render"]["dpi"],
                "region_pixel_size": [
                    figure["asset"]["width_px"],
                    figure["asset"]["height_px"],
                ],
                "caption_id": figure["caption"]["caption_id"],
                "caption_text_sha256": figure["caption"]["text_sha256"],
                "observation": (
                    "区域图完整覆盖混合位图/矢量 Figure，未烧入 caption，"
                    "未见明显无关正文；native asset 同时保留。"
                ),
                "files": [
                    page_path,
                    marked_path,
                    native_path,
                    region_path,
                    contact_path,
                ],
            }
        else:
            reasons = [
                item
                for item in manifest["figure_rejections"]
                if item["page"] == page_number
                and item["reason"]
                == "cross_page_figure_continuation_explicitly_detected"
            ]
            if not reasons:
                raise RuntimeError("expected continued rejection missing")
            marked = page_image.copy()
            draw = ImageDraw.Draw(marked)
            draw.rectangle(
                (3, 3, marked.width - 4, marked.height - 4),
                outline=(220, 0, 0),
                width=4,
            )
            draw.text(
                (16, 16),
                "REJECTED: FIGURE CONTINUED ON NEXT PAGE",
                fill=(220, 0, 0),
            )
            marked_path = args.output / f"{prefix}-rejection.png"
            marked.save(marked_path, format="PNG", optimize=True)
            contact_path = args.output / f"{prefix}-contact.png"
            _contact(
                [
                    ("ORIGINAL PAGE", page_image),
                    ("SAFE REJECTION", marked),
                ],
                contact_path,
            )
            record = {
                "paper_id": paper_id,
                "page": page_number,
                "status": "rejected",
                "reason": "cross_page_figure_continuation_explicitly_detected",
                "observation": (
                    "页面明确出现 continued on next page；自动模式保守拒绝，"
                    "没有生成或引用 region asset。"
                ),
                "files": [page_path, marked_path, contact_path],
            }
        records.append(record)

    file_records = []
    for path in sorted(args.output.glob("*.png")):
        with Image.open(path) as image:
            file_records.append(
                {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "pixel_size": [image.width, image.height],
                    "mode": image.mode,
                }
            )
    for record in records:
        record["files"] = [item.name for item in record["files"]]
    evidence_manifest = {
        "schema_version": "paper2md-phase4-auto-visual-evidence-v1",
        "source_package_base_commit": (
            "25e4ecea02979cf7dcb56ab2d280425bc56e74e2"
        ),
        "source_runtime": "phase4-auto-runtime/auto-final-v2/first",
        "render_scale_for_page_evidence": scale,
        "targets": records,
        "files": file_records,
        "contains_pdf": False,
        "git_commit_candidate": False,
    }
    manifest_path = args.output / "visual_evidence_manifest.json"
    manifest_path.write_text(
        json.dumps(
            evidence_manifest, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
