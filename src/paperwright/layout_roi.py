"""Content-ROI review contract for non-destructive layout analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .exceptions import ContractValidationError
from .layout_models import NormalizedBBox
from .models import PhysicalDocument

CONTENT_ROI_CONTRACT_VERSION = "paperwright-content-roi-v0.1"


def content_roi_contract(
    document: PhysicalDocument,
    rois: Mapping[int, NormalizedBBox],
    *,
    review_status: str = "proposed",
    reviewer: str | None = None,
) -> dict[str, object]:
    if review_status not in {"proposed", "confirmed"}:
        raise ContractValidationError(
            "content ROI review_status must be proposed or confirmed"
        )
    if review_status == "confirmed" and not (reviewer or "").strip():
        raise ContractValidationError(
            "confirmed content ROI requires reviewer"
        )
    expected = {page.page_index for page in document.pages}
    if set(rois) != expected:
        raise ContractValidationError(
            "content ROI pages must exactly match the PDF pages"
        )
    return {
        "contract_version": CONTENT_ROI_CONTRACT_VERSION,
        "source_sha256": document.source_sha256,
        "review_status": review_status,
        "reviewer": reviewer,
        "coordinate_system": "top-left/original-page-normalized/y-down",
        "destructive_crop": False,
        "pages": [
            {
                "page_index": page.page_index,
                "content_bbox": rois[page.page_index].to_dict(),
            }
            for page in document.pages
        ],
    }


def canonical_content_roi_json(value: Mapping[str, object]) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def content_roi_review_instructions() -> str:
    return """# PaperWright Content ROI 复核

1. 逐页打开 `page-XXXX/content-roi.png`。
2. 红框必须包含正文、标题、作者信息、脚注、Figure、Table 和 caption。
3. 红框应排除重复页眉、页脚、页码、期刊标识和边缘装饰。
4. 不确定时扩大红框；不得为追求紧凑而裁掉真实内容。
5. 在 `content-roi.json` 中修正各页 `content_bbox`，坐标仍相对于完整原页。
6. 确认后把 `review_status` 改为 `confirmed`，并填写非空 `reviewer`。
7. 使用确认文件重新运行 `layout-prepare --content-roi-json ...`。

Content ROI 只是分析掩膜，不会裁剪 PDF，也不会改变任何原始坐标。
"""


def load_confirmed_content_rois(
    path: str | Path,
    document: PhysicalDocument,
) -> tuple[dict[int, NormalizedBBox], str]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if value.get("contract_version") != CONTENT_ROI_CONTRACT_VERSION:
        raise ContractValidationError("content ROI contract_version mismatch")
    if value.get("source_sha256") != document.source_sha256:
        raise ContractValidationError("content ROI source_sha256 mismatch")
    if value.get("review_status") != "confirmed":
        raise ContractValidationError(
            "content ROI must be AI/human confirmed before use"
        )
    reviewer = value.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ContractValidationError("confirmed content ROI requires reviewer")
    pages = value.get("pages")
    if not isinstance(pages, list):
        raise ContractValidationError("content ROI pages must be a list")
    rois: dict[int, NormalizedBBox] = {}
    for item in pages:
        if not isinstance(item, dict):
            raise ContractValidationError("content ROI page must be an object")
        page_index = item.get("page_index")
        bbox = item.get("content_bbox")
        if not isinstance(page_index, int) or not isinstance(bbox, dict):
            raise ContractValidationError(
                "content ROI page_index/content_bbox invalid"
            )
        if page_index in rois:
            raise ContractValidationError("duplicate content ROI page_index")
        rois[page_index] = NormalizedBBox.from_dict(bbox)
    expected = {page.page_index for page in document.pages}
    if set(rois) != expected:
        raise ContractValidationError(
            "content ROI pages must exactly match the PDF pages"
        )
    return rois, f"confirmed:{reviewer.strip()}"
