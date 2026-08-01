"""Deterministic and heuristic output-quality checks for hybrid conversion."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Any, Sequence
from urllib.parse import unquote

from .layout_models import FinalLayout, LayoutTask
from .models import PhysicalDocument


_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿΑ-ω0-9]+(?:[-'][A-Za-z0-9]+)*")
_REPEATED_WORD = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.IGNORECASE)
_SPACED_CAPS = re.compile(r"\b(?:[A-Z]{1,4}\s+){2,}[A-Z]{1,4}\b")
_PANEL_OR_NUMBER = re.compile(r"^(?:[A-Z]|\d+(?:\.\d+)?%?)$")
_GLUED_SCIENTIFIC_TOKEN = re.compile(
    r"(?:\b[A-Z]{2,}\d+[A-Z]?|[Α-Ωα-ω])(?=[a-z]{2,}\b)"
)
_GLUED_SYMBOL_WORD = re.compile(
    r"(?:\d+(?:\.\d+)?%|[A-Za-z0-9]\+)(?=[A-Za-z]{2,}\b)"
)
_GLUED_ACRONYM_PROSE = re.compile(
    r"\b[A-Z]{3,}(?=(?:and|or|with|within|without|from|to|in|on|by|of)\b)"
)
_MAX_FINDINGS = 50


def _snippet(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _finding(record: dict[str, Any], code: str) -> dict[str, Any]:
    return {
        "code": code,
        "page": record["page_index"] + 1,
        "region_id": record["region_id"],
        "paragraph_index": record["paragraph_index"],
        "snippet": _snippet(record["text"]),
    }


def _looks_like_short_heading(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return bool(letters) and (
        sum(character.isupper() for character in letters) / len(letters) >= 0.85
    )


def analyze_markdown_text(
    paragraphs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    broken: list[dict[str, Any]] = []
    repeated: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    figure_labels: list[dict[str, Any]] = []

    for index, record in enumerate(paragraphs):
        text = record["text"]
        words = _WORD.findall(text)
        if _SPACED_CAPS.search(text) or (
            index + 1 < len(paragraphs)
            and text.rstrip().endswith("-")
            and paragraphs[index + 1]["text"][:1].islower()
        ):
            broken.append(_finding(record, "suspected_broken_word"))
        if _REPEATED_WORD.search(text):
            repeated.append(_finding(record, "repeated_word"))
        if (
            record["role"] == "body"
            and not record.get("is_bold", False)
            and not _looks_like_short_heading(text)
            and 0 < len(words) <= 3
            and len(text) <= 40
        ):
            fragments.append(_finding(record, "short_body_fragment"))
        if len(words) >= 6:
            label_count = sum(
                bool(_PANEL_OR_NUMBER.fullmatch(word)) for word in words
            )
            if label_count / len(words) >= 0.55:
                figure_labels.append(
                    _finding(record, "suspected_figure_label_leak")
                )

    findings = broken + repeated + fragments
    return {
        "status": "warning" if findings else "pass",
        "suspected_broken_word_count": len(broken),
        "repeated_word_count": len(repeated),
        "short_body_fragment_count": len(fragments),
        "findings": findings[:_MAX_FINDINGS],
        "figure_label_leakage": {
            "status": "warning" if figure_labels else "pass",
            "suspected_count": len(figure_labels),
            "findings": figure_labels[:_MAX_FINDINGS],
        },
    }


def analyze_word_spacing(
    paragraphs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Audit high-confidence residual joins and provenance-backed repairs."""

    glued: list[dict[str, Any]] = []
    repairs: Counter[str] = Counter()
    soft_breaks: list[dict[str, Any]] = []
    for record in paragraphs:
        text = record["text"]
        matches = list(_GLUED_SCIENTIFIC_TOKEN.finditer(text))
        matches.extend(_GLUED_SYMBOL_WORD.finditer(text))
        matches.extend(_GLUED_ACRONYM_PROSE.finditer(text))
        if matches:
            finding = _finding(record, "suspected_missing_word_space")
            finding["matches"] = sorted(
                {text[item.start() : item.end() + 16] for item in matches}
            )[:10]
            glued.append(finding)
        raw_events = record.get("reconstruction_events", ())
        if not isinstance(raw_events, (list, tuple)):
            continue
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            code = event.get("code")
            if not isinstance(code, str):
                continue
            repairs[code] += 1
            if code == "joined_explicit_pdf_soft_break":
                finding = _finding(record, "ambiguous_pdf_soft_break_join")
                finding["before"] = _snippet(str(event.get("before", "")))
                finding["after"] = _snippet(str(event.get("after", "")))
                soft_breaks.append(finding)

    return {
        "status": "warning" if glued else "pass",
        "suspected_missing_space_count": len(glued),
        "geometric_space_insertion_count": repairs[
            "inserted_geometric_word_space"
        ],
        "geometric_fragment_join_count": repairs[
            "collapsed_tight_same_font_fragment_gap"
        ],
        "ambiguous_soft_break_join_count": len(soft_breaks),
        "repairs_by_code": dict(sorted(repairs.items())),
        "findings": glued[:_MAX_FINDINGS],
        "soft_break_findings": soft_breaks[:_MAX_FINDINGS],
    }


def analyze_title(title: str, article_text: str) -> dict[str, Any]:
    headings = [
        line for line in article_text.splitlines() if line.startswith("# ")
    ]
    broken = bool(_SPACED_CAPS.search(title)) or title.rstrip().endswith("-")
    valid = len(headings) == 1 and headings[0] == f"# {title}" and not broken
    return {
        "status": "pass" if valid else "warning",
        "title": title,
        "h1_count": len(headings),
        "matches_extracted_title": bool(headings and headings[0] == f"# {title}"),
        "suspected_fragmentation": broken or len(headings) != 1,
    }


def analyze_image_links(article_path: Path, images_dir: Path) -> dict[str, Any]:
    article_text = article_path.read_text(encoding="utf-8")
    root = article_path.parent.resolve()
    links = [unquote(item.strip()) for item in _MARKDOWN_IMAGE.findall(article_text)]
    missing: list[str] = []
    unsafe: list[str] = []
    resolved_links: set[str] = set()
    for link in links:
        target = (root / link).resolve()
        try:
            relative = target.relative_to(root).as_posix()
        except ValueError:
            unsafe.append(link)
            continue
        resolved_links.add(relative)
        if not target.is_file():
            missing.append(link)
    image_files = {
        item.resolve().relative_to(root).as_posix()
        for item in images_dir.glob("*")
        if item.is_file()
    }
    orphaned = sorted(image_files - resolved_links)
    valid = not missing and not unsafe and not orphaned
    return {
        "status": "pass" if valid else "fail",
        "referenced_count": len(links),
        "image_file_count": len(image_files),
        "missing": sorted(set(missing)),
        "unsafe": sorted(set(unsafe)),
        "orphaned": orphaned,
    }


def analyze_layout_elements(
    tasks: Sequence[LayoutTask],
    layouts: Sequence[FinalLayout],
    document: PhysicalDocument,
) -> dict[str, Any]:
    unassigned: list[dict[str, Any]] = []
    duplicated: list[dict[str, Any]] = []
    eligible_count = 0
    used_count = 0
    intentionally_discarded_count = 0
    unassigned_count = 0

    for task, layout, page in zip(
        tasks, layouts, document.pages, strict=True
    ):
        text_ids = {
            element.element_id for element in page.elements if element.kind == "text"
        }
        candidate_elements = {
            candidate.candidate_id: {
                element_id
                for element_id in candidate.source_element_ids
                if element_id in text_ids
            }
            for candidate in task.candidates
        }
        eligible = {
            element_id
            for element_ids in candidate_elements.values()
            for element_id in element_ids
        }
        discarded_candidate_ids = {
            candidate_id
            for action in layout.actions
            if action.action == "discard"
            for candidate_id in action.source_candidate_ids
        }
        discarded = {
            element_id
            for candidate_id in discarded_candidate_ids
            for element_id in candidate_elements.get(candidate_id, ())
        }
        assignments: dict[str, list[str]] = defaultdict(list)
        for region in layout.regions:
            if region.content_class == "exclude":
                continue
            for element_id in region.source_element_ids:
                assignments[element_id].append(region.region_id)
        missing = sorted(eligible - set(assignments) - discarded)
        remaining = max(0, _MAX_FINDINGS - len(unassigned))
        for element_id in missing[:remaining]:
            unassigned.append(
                {
                    "page": task.page.page_index + 1,
                    "element_id": element_id,
                }
            )
        for element_id, region_ids in sorted(assignments.items()):
            unique_regions = sorted(set(region_ids))
            if len(unique_regions) > 1:
                duplicated.append(
                    {
                        "page": task.page.page_index + 1,
                        "element_id": element_id,
                        "region_ids": unique_regions,
                    }
                )
        eligible_count += len(eligible)
        used_count += len(eligible & set(assignments))
        intentionally_discarded_count += len(discarded - set(assignments))
        unassigned_count += len(missing)

    return {
        "coverage": {
            "status": "pass" if unassigned_count == 0 else "warning",
            "eligible_text_object_count": eligible_count,
            "used_text_object_count": used_count,
            "intentionally_discarded_text_object_count": (
                intentionally_discarded_count
            ),
            "unassigned_count": unassigned_count,
            "findings": unassigned[:_MAX_FINDINGS],
        },
        "uniqueness": {
            "status": "pass" if not duplicated else "warning",
            "duplicate_assignment_count": len(duplicated),
            "findings": duplicated[:_MAX_FINDINGS],
        },
    }


def analyze_manifest_inventory(
    root: Path,
    output_paths: Sequence[Path],
) -> dict[str, Any]:
    resolved_root = root.resolve()
    expected = [path.resolve() for path in output_paths]
    counts = Counter(expected)
    duplicates = sorted(
        path.relative_to(resolved_root).as_posix()
        for path, count in counts.items()
        if count > 1
    )
    missing = sorted(
        path.relative_to(resolved_root).as_posix()
        for path in counts
        if not path.is_file()
    )
    actual = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    unlisted = sorted(
        path.relative_to(resolved_root).as_posix()
        for path in actual - set(counts)
    )
    valid = not duplicates and not missing and not unlisted
    return {
        "status": "pass" if valid else "fail",
        "expected_output_count": len(counts),
        "actual_output_count": len(actual),
        "duplicate_paths": duplicates,
        "missing_paths": missing,
        "unlisted_paths": unlisted,
    }


def analyze_native_object_diagnostics(
    document: PhysicalDocument,
) -> dict[str, Any]:
    diagnostics = document.metadata.get("degenerate_object_handling", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    raw_counts = diagnostics.get("counts", {})
    counts = {
        str(code): int(count)
        for code, count in raw_counts.items()
        if isinstance(code, str) and isinstance(count, int) and count >= 0
    } if isinstance(raw_counts, dict) else {}
    risky_codes = {
        code for code in counts if code.startswith("unplaced_degenerate_")
    }
    findings: list[dict[str, Any]] = []
    raw_pages = diagnostics.get("pages", [])
    if isinstance(raw_pages, list):
        for page in raw_pages:
            if not isinstance(page, dict) or not isinstance(page.get("page"), int):
                continue
            page_counts = page.get("counts", {})
            if not isinstance(page_counts, dict):
                continue
            for code in sorted(risky_codes):
                count = page_counts.get(code, 0)
                if isinstance(count, int) and count > 0:
                    findings.append(
                        {
                            "code": code,
                            "page": page["page"],
                            "count": count,
                        }
                    )
    unplaced_count = sum(counts.get(code, 0) for code in risky_codes)
    ignored_count = sum(
        count
        for code, count in counts.items()
        if code.startswith("ignored_degenerate_")
    )
    return {
        "status": "warning" if unplaced_count else "pass",
        "policy_version": diagnostics.get("policy_version"),
        "ignored_safe_object_count": ignored_count,
        "unplaced_risk_object_count": unplaced_count,
        "counts": dict(sorted(counts.items())),
        "findings": findings[:_MAX_FINDINGS],
    }


def analyze_markdown_exclusions(
    document: PhysicalDocument,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for page in document.pages:
        for element in page.elements:
            reason = element.metadata.get("markdown_excluded_reason")
            if not isinstance(reason, str) or not reason:
                continue
            reasons[reason] += 1
            if len(findings) < _MAX_FINDINGS:
                findings.append(
                    {
                        "code": reason,
                        "page": page.page_index + 1,
                        "element_id": element.element_id,
                        "bbox": element.bbox.to_dict(),
                        "text_codepoints": [
                            f"U+{ord(character):04X}"
                            for character in (element.text or "")
                        ],
                    }
                )
    return {
        "status": "pass",
        "excluded_count": sum(reasons.values()),
        "by_reason": dict(sorted(reasons.items())),
        "findings": findings,
    }
