"""Stable reader index and public Markdown anchors for reviewed documents."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, MutableMapping, Sequence

from .article_model import article_model_to_reader
from .article_tree import (
    article_tree_to_article_model,
    build_final_article_tree,
    reviewed_projection_sha256,
)
from .exceptions import ContractValidationError
from .models import BBox, PhysicalDocument
from .reader_contract import (
    BLOCK_FINGERPRINT_VERSION,
    MARKDOWN_ANCHOR_CONTRACT_VERSION,
    READER_CONTRACT_VERSION,
    VALID_ASSET_KINDS as _VALID_ASSET_KINDS,
    VALID_BLOCK_KINDS as _VALID_BLOCK_KINDS,
    canonical_payload as _canonical_payload,
    canonical_reader_json,
    normalized_visible_text as _normalized_visible_text,
    stable_reader_id as _stable_id,
    validate_reader_index,
    visible_block_fingerprint as _fingerprint,
)


_TRACE_RE = re.compile(
    r"^<!-- layout-region: (?P<region_id>[^;]+); role: (?P<role>[^;]+); "
    r"page: (?P<page>[0-9]+); element-count: [0-9]+; "
    r"elements-sha256: (?P<elements_sha256>[0-9a-f]{64}); "
    r"provenance-ref: page/[0-9]+/region/[^ /]+"
    r"(?:/paragraph/(?P<paragraph_index>[0-9]+))? -->$"
)
_INTERNAL_COMMENT_PREFIXES = (
    "<!-- page:",
    "<!-- caption-for:",
    "<!-- cross-page-continuation:",
    "<!-- body-continuation:",
    "<!-- paragraph-continuation:",
)
_MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^]]*\]\((?P<path>[^)]+)\)$")
_CAPTION_LABEL_RE = re.compile(
    r"^(?P<label>(?:fig(?:ure)?\.?|table)\s+S?[0-9]+[A-Za-z]?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Trace:
    page_index: int
    region_id: str
    role: str
    paragraph_index: int | None
    elements_sha256: str


@dataclass(frozen=True)
class ReaderCompilation:
    """Final anchored Markdown plus the graph used to create reader.json."""

    markdown_lines: tuple[str, ...]
    blocks: tuple[dict[str, Any], ...]
    assets: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]
    markdown_by_id: tuple[tuple[str, str], ...]
    source_sha256: str
    physical_document_sha256: str

    def markdown_text(self) -> str:
        return "\n".join(self.markdown_lines).rstrip() + "\n"

    def reader_index(self, *, source_sha256: str) -> dict[str, Any]:
        return article_model_to_reader(
            self.article_model(source_sha256=source_sha256)
        )

    def article_model(self, *, source_sha256: str) -> dict[str, Any]:
        return article_tree_to_article_model(
            self.article_tree(source_sha256=source_sha256)
        )

    def article_tree(self, *, source_sha256: str) -> dict[str, Any]:
        """Build the canonical tree used by compatibility projections."""

        if source_sha256 != self.source_sha256:
            raise ContractValidationError(
                "reader compilation source_sha256 与 PhysicalDocument 不一致"
            )
        markdown_by_id = dict(self.markdown_by_id)
        projection_sha256 = reviewed_projection_sha256(
            blocks=self.blocks,
            markdown_by_id=markdown_by_id,
            assets=self.assets,
            relations=self.relations,
        )
        return build_final_article_tree(
            source_sha256=source_sha256,
            physical_document_sha256=self.physical_document_sha256,
            structure_input_kind="reviewed_projection",
            structure_input_sha256=projection_sha256,
            blocks=self.blocks,
            markdown_by_id=markdown_by_id,
            assets=self.assets,
            relations=self.relations,
        )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_bbox(bbox: BBox, *, width: float, height: float) -> dict[str, float]:
    return {
        "x": round(bbox.x / width, 8),
        "y": round(bbox.y / height, 8),
        "width": round(bbox.width / width, 8),
        "height": round(bbox.height / height, 8),
    }


def _union_element_bbox(
    document: PhysicalDocument,
    page_index: int,
    element_ids: Sequence[str],
) -> dict[str, float] | None:
    page = document.pages[page_index]
    wanted = set(element_ids)
    boxes = [item.bbox for item in page.elements if item.element_id in wanted]
    if not boxes:
        return None
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return _normalized_bbox(
        BBox(left, top, right - left, bottom - top),
        width=page.width,
        height=page.height,
    )


def _source_lookups(
    provenance_pages: Sequence[MutableMapping[str, Any]],
) -> tuple[
    dict[tuple[int, str], MutableMapping[str, Any]],
    dict[tuple[int, str, int], MutableMapping[str, Any]],
]:
    regions: dict[tuple[int, str], MutableMapping[str, Any]] = {}
    paragraphs: dict[tuple[int, str, int], MutableMapping[str, Any]] = {}
    for page in provenance_pages:
        page_index = int(page["page_index"])
        for region in page["regions"]:
            region_key = (page_index, str(region["region_id"]))
            regions[region_key] = region
            for paragraph in region.get("paragraphs", ()):
                paragraphs[
                    (
                        page_index,
                        str(region["region_id"]),
                        int(paragraph["paragraph_index"]),
                    )
                ] = paragraph
    return regions, paragraphs


def _source_span_sort_key(span: Mapping[str, Any]) -> tuple[object, ...]:
    bbox = span["bbox"]
    return (
        span["page_index"],
        span["region_id"] or "",
        -1 if span["paragraph_index"] is None else span["paragraph_index"],
        bbox["y"],
        bbox["x"],
        span["elements_sha256"],
    )


def _trace_span(
    trace: _Trace,
    *,
    document: PhysicalDocument,
    regions: Mapping[tuple[int, str], Mapping[str, Any]],
    paragraphs: Mapping[tuple[int, str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    paragraph = (
        paragraphs.get(
            (trace.page_index, trace.region_id, trace.paragraph_index)
        )
        if trace.paragraph_index is not None
        else None
    )
    bbox = None
    if paragraph is not None:
        bbox = _union_element_bbox(
            document,
            trace.page_index,
            paragraph.get("source_element_ids", ()),
        )
    region = regions.get((trace.page_index, trace.region_id))
    if bbox is None and region is not None:
        raw_bbox = region.get("bbox")
        if isinstance(raw_bbox, dict):
            bbox = dict(raw_bbox)
    if bbox is None:
        raise ContractValidationError(
            f"reader source span 缺少 bbox: page={trace.page_index} "
            f"region={trace.region_id}"
        )
    return {
        "page_index": trace.page_index,
        "bbox": bbox,
        "region_id": trace.region_id,
        "paragraph_index": trace.paragraph_index,
        "elements_sha256": trace.elements_sha256,
    }


def _title_spans(
    document: PhysicalDocument,
    title_element_ids: Sequence[str],
) -> list[dict[str, Any]]:
    by_page: dict[int, list[str]] = {}
    wanted = set(title_element_ids)
    for page in document.pages:
        ids = [
            item.element_id for item in page.elements if item.element_id in wanted
        ]
        if ids:
            by_page[page.page_index] = ids
    spans: list[dict[str, Any]] = []
    for page_index, element_ids in sorted(by_page.items()):
        bbox = _union_element_bbox(document, page_index, element_ids)
        if bbox is None:
            continue
        spans.append(
            {
                "page_index": page_index,
                "bbox": bbox,
                "region_id": None,
                "paragraph_index": None,
                "elements_sha256": _sha256_text("\0".join(sorted(element_ids))),
            }
        )
    return spans


def _block_kind(role: str, *, image: bool) -> str:
    if image:
        return "visual_slot"
    return role if role in _VALID_BLOCK_KINDS else "unknown"


def _asset_kind(role: str) -> str:
    return role if role in _VALID_ASSET_KINDS else "unknown"


def _caption_label(markdown: str) -> str | None:
    visible = _normalized_visible_text(markdown)
    match = _CAPTION_LABEL_RE.match(visible)
    return match.group("label").rstrip(".") if match is not None else None


def _relation(
    relation_type: str,
    source_id: str,
    target_id: str,
    source_sha256: str,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "id": _stable_id(
            "rel",
            source_sha256,
            {
                "type": relation_type,
                "source_id": source_id,
                "target_id": target_id,
                "label": label,
            },
        ),
        "type": relation_type,
        "source_id": source_id,
        "target_id": target_id,
        "label": label,
    }


def compile_reviewed_article(
    lines: Sequence[str],
    *,
    document: PhysicalDocument,
    title_element_ids: Sequence[str],
    provenance_pages: Sequence[MutableMapping[str, Any]],
    image_records: Sequence[MutableMapping[str, Any]],
) -> ReaderCompilation:
    """Compile traced writer lines into public anchors and a reader graph."""

    if not lines or not lines[0].startswith("# "):
        raise ContractValidationError("reader article 缺少 H1 标题")
    regions, paragraphs = _source_lookups(provenance_pages)
    image_by_path = {str(item["path"]): item for item in image_records}

    title_spans = _title_spans(document, title_element_ids)
    title_identity: dict[str, object] = {
        "kind": "title",
        "source_spans": title_spans,
    }
    if not title_spans:
        title_identity["fallback_visible_text_sha256"] = _fingerprint(
            lines[0]
        )["visible_text_sha256"]
    title_id = _stable_id("blk", document.source_sha256, title_identity)
    title_marker = f'<!-- pwwd:block id="{title_id}" kind="title" -->'
    output_lines: list[str] = [title_marker, lines[0], ""]
    blocks: list[dict[str, Any]] = [
        {
            "id": title_id,
            "kind": "title",
            "order": 1,
            "anchor": {"syntax": "pwwd:block", "id": title_id},
            "fingerprint": _fingerprint(lines[0]),
            "source_spans": title_spans,
            "asset_id": None,
        }
    ]
    assets_pending: list[
        tuple[dict[str, Any], MutableMapping[str, Any], tuple[_Trace, ...]]
    ] = []
    relations: list[dict[str, Any]] = []
    block_by_region: dict[tuple[int, str], list[str]] = {}
    block_text: dict[str, str] = {title_id: lines[0]}
    pending: list[_Trace] = []

    start_index = 2 if len(lines) > 1 and not lines[1] else 1
    for line in lines[start_index:]:
        trace_match = _TRACE_RE.match(line)
        if trace_match is not None:
            pending.append(
                _Trace(
                    page_index=int(trace_match.group("page")) - 1,
                    region_id=trace_match.group("region_id"),
                    role=trace_match.group("role"),
                    paragraph_index=(
                        int(trace_match.group("paragraph_index"))
                        if trace_match.group("paragraph_index") is not None
                        else None
                    ),
                    elements_sha256=trace_match.group("elements_sha256"),
                )
            )
            continue
        if line.lstrip().startswith(_INTERNAL_COMMENT_PREFIXES):
            continue
        if not line:
            if output_lines and output_lines[-1]:
                output_lines.append("")
            continue
        if not pending:
            if line.lstrip().startswith("<!--"):
                output_lines.append(line)
                continue
            raise ContractValidationError(
                f"reader article 内容缺少 source trace: {line[:80]}"
            )

        unique_spans: dict[str, dict[str, Any]] = {}
        for trace in pending:
            span = _trace_span(
                trace,
                document=document,
                regions=regions,
                paragraphs=paragraphs,
            )
            unique_spans[_canonical_payload(span)] = span
        source_spans = sorted(unique_spans.values(), key=_source_span_sort_key)
        image_match = _MARKDOWN_IMAGE_RE.match(line)
        kind = _block_kind(pending[0].role, image=image_match is not None)
        identity = {"kind": kind, "source_spans": source_spans}

        asset_id: str | None = None
        if image_match is not None:
            image_path = image_match.group("path")
            image_record = image_by_path.get(image_path)
            if image_record is None:
                raise ContractValidationError(
                    f"reader image record 缺失: {image_path}"
                )
            asset_id = _stable_id(
                "ast",
                document.source_sha256,
                {
                    "kind": _asset_kind(str(image_record["role"])),
                    "source_spans": source_spans,
                },
            )
            block_id = _stable_id(
                "slot",
                document.source_sha256,
                {"asset_id": asset_id, "source_spans": source_spans},
            )
            marker = (
                f'<!-- pwwd:slot id="{block_id}" asset="{asset_id}" -->'
            )
            anchor = {"syntax": "pwwd:slot", "id": block_id}
            image_record["reader_asset_id"] = asset_id
            assets_pending.append(
                (
                    {
                        "id": asset_id,
                        "kind": _asset_kind(str(image_record["role"])),
                        "path": image_path,
                        "sha256": str(image_record["sha256"]),
                        "size_bytes": int(image_record["size_bytes"]),
                        "width_px": int(image_record["width_px"]),
                        "height_px": int(image_record["height_px"]),
                        "display_label": None,
                        "caption_block_id": None,
                        "placement_block_id": block_id,
                        "source_spans": source_spans,
                    },
                    image_record,
                    tuple(pending),
                )
            )
            relations.append(
                _relation(
                    "places",
                    block_id,
                    asset_id,
                    document.source_sha256,
                )
            )
        else:
            block_id = _stable_id("blk", document.source_sha256, identity)
            marker = (
                f'<!-- pwwd:block id="{block_id}" kind="{kind}" -->'
            )
            anchor = {"syntax": "pwwd:block", "id": block_id}

        block = {
            "id": block_id,
            "kind": kind,
            "order": len(blocks) + 1,
            "anchor": anchor,
            "fingerprint": _fingerprint(line),
            "source_spans": source_spans,
            "asset_id": asset_id,
        }
        blocks.append(block)
        block_text[block_id] = line
        output_lines.extend((marker, line, ""))
        for trace in pending:
            block_by_region.setdefault(
                (trace.page_index, trace.region_id), []
            ).append(block_id)
            region = regions.get((trace.page_index, trace.region_id))
            if region is not None:
                ids = region.setdefault("article_block_ids", [])
                if block_id not in ids:
                    ids.append(block_id)
            if trace.paragraph_index is not None:
                paragraph = paragraphs.get(
                    (trace.page_index, trace.region_id, trace.paragraph_index)
                )
                if paragraph is not None:
                    paragraph["article_block_id"] = block_id
        pending.clear()

    if pending:
        raise ContractValidationError("reader article 末尾存在未消费 trace")

    assets: list[dict[str, Any]] = []
    for asset, image_record, traces in assets_pending:
        binding = image_record.get("caption_binding")
        if isinstance(binding, dict):
            caption_key = (
                int(binding["caption_page"]) - 1,
                str(binding["caption_region_id"]),
            )
            candidates = block_by_region.get(caption_key, ())
            if candidates:
                caption_block_id = candidates[0]
                asset["caption_block_id"] = caption_block_id
                asset["display_label"] = _caption_label(
                    block_text[caption_block_id]
                )
                relations.append(
                    _relation(
                        "caption-of",
                        caption_block_id,
                        str(asset["id"]),
                        document.source_sha256,
                        label=asset["display_label"],
                    )
                )
        image_record["reader_placement_block_id"] = asset[
            "placement_block_id"
        ]
        image_record["reader_caption_block_id"] = asset[
            "caption_block_id"
        ]
        for trace in traces:
            region = regions.get((trace.page_index, trace.region_id))
            if region is not None:
                region["reader_asset_id"] = asset["id"]
        assets.append(asset)

    while output_lines and not output_lines[-1]:
        output_lines.pop()
    return ReaderCompilation(
        markdown_lines=tuple(output_lines),
        blocks=tuple(blocks),
        assets=tuple(sorted(assets, key=lambda item: str(item["id"]))),
        relations=tuple(sorted(relations, key=lambda item: str(item["id"]))),
        markdown_by_id=tuple(
            (str(block["id"]), block_text[str(block["id"])])
            for block in blocks
        ),
        source_sha256=document.source_sha256,
        physical_document_sha256=document.deterministic_sha256(),
    )
