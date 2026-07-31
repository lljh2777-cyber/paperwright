"""Conservative native-text reference-section detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

REFERENCE_MODES = {"keep", "omit", "separate"}

_REFERENCE_HEADING_KEYS = {
    "references",
    "referencesandnotes",
    "bibliography",
    "literaturecited",
    "workscited",
    "参考文献",
}
_REMOVABLE_BACK_MATTER_PREFIXES = (
    "acknowledgement",
    "acknowledgements",
    "acknowledgment",
    "acknowledgments",
    "authorcontribution",
    "authorcontributions",
    "authorinformation",
    "competinginterest",
    "competinginterests",
    "conflictofinterest",
    "conflictsofinterest",
    "dataavailability",
    "funding",
    "致谢",
    "作者贡献",
    "数据可用性",
)
_SUPPLEMENTARY_PREFIXES = (
    "supplementaryinformation",
    "supplementarymaterials",
    "补充材料",
)
_NUMBERED_ENTRY = re.compile(
    r"(?:^|(?<=\s))(?:\[\d{1,4}\]|\d{1,4}[.)])(?:\s|$)"
)
_YEAR = re.compile(r"\b(?:18|19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_DOI = re.compile(r"\b(?:doi\s*:|https?://doi\.org/|10\.\d{4,9}/)", re.IGNORECASE)
_PMID = re.compile(r"\bpmid\s*:", re.IGNORECASE)
_AUTHOR = re.compile(r"\b(?:et\s+al\.?|and\s+[A-Z][a-z]+)\b", re.IGNORECASE)
_JOURNAL = re.compile(
    r"\b(?:vol\.?|volume|pp?\.?|journal|proceedings|proc\.?|"
    r"nature|science|cell|lancet|immunity|biol\.?|med\.?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReferenceParagraph:
    page_index: int
    region_id: str
    paragraph_index: int
    text: str

    @property
    def key(self) -> tuple[int, str, int]:
        return self.page_index, self.region_id, self.paragraph_index


@dataclass(frozen=True)
class ReferenceSection:
    start_index: int
    end_index: int
    start: ReferenceParagraph
    end: ReferenceParagraph | None
    evidence_score: int
    evidence_paragraphs: int
    detection_method: str


@dataclass(frozen=True)
class _ReferenceSignals:
    numbered: int
    years: int
    doi_or_pmid: int
    author: int
    journal: int

    @property
    def score(self) -> int:
        return (
            min(6, self.numbered * 2)
            + min(3, self.years)
            + min(2, self.doi_or_pmid)
            + self.author
            + self.journal
        )

    @property
    def strong_entry_run(self) -> bool:
        if self.numbered >= 2 and self.years >= 1:
            return bool(self.doi_or_pmid or self.journal)
        if self.numbered >= 1 and self.years >= 2:
            return bool(self.doi_or_pmid or self.author or self.journal)
        return False


def _heading_key(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())


def is_reference_heading(text: str) -> bool:
    return _heading_key(text) in _REFERENCE_HEADING_KEYS


def _is_post_reference_heading(text: str) -> bool:
    return is_removable_back_matter_heading(
        text
    ) or is_supplementary_heading(text)


def is_removable_back_matter_heading(text: str) -> bool:
    key = _heading_key(text)
    return any(
        key.startswith(prefix)
        for prefix in _REMOVABLE_BACK_MATTER_PREFIXES
    )


def is_supplementary_heading(text: str) -> bool:
    key = _heading_key(text)
    return any(key.startswith(prefix) for prefix in _SUPPLEMENTARY_PREFIXES)


def removable_back_matter_keys(
    paragraphs: Sequence[ReferenceParagraph],
    start_index: int,
) -> frozenset[tuple[int, str, int]]:
    """Select administrative back matter while preserving supplements."""

    removing = False
    keys: set[tuple[int, str, int]] = set()
    for paragraph in paragraphs[start_index:]:
        if is_supplementary_heading(paragraph.text):
            removing = False
        elif is_removable_back_matter_heading(paragraph.text):
            removing = True
        if removing:
            keys.add(paragraph.key)
    return frozenset(keys)


def _reference_signals(text: str) -> _ReferenceSignals:
    normalized = " ".join(text.split())
    return _ReferenceSignals(
        numbered=len(_NUMBERED_ENTRY.findall(normalized)),
        years=len(_YEAR.findall(normalized)),
        doi_or_pmid=(
            len(_DOI.findall(normalized)) + len(_PMID.findall(normalized))
        ),
        author=int(_AUTHOR.search(normalized) is not None),
        journal=int(_JOURNAL.search(normalized) is not None),
    )


def _reference_evidence(text: str) -> int:
    return _reference_signals(text).score


def _reference_end(
    paragraphs: Sequence[ReferenceParagraph],
    start_index: int,
) -> int:
    for index in range(start_index + 1, len(paragraphs)):
        if _is_post_reference_heading(paragraphs[index].text):
            return index
    return len(paragraphs)


def _make_section(
    paragraphs: Sequence[ReferenceParagraph],
    *,
    start_index: int,
    evidence: Sequence[int],
    detection_method: str,
) -> ReferenceSection:
    end_index = _reference_end(paragraphs, start_index)
    return ReferenceSection(
        start_index=start_index,
        end_index=end_index,
        start=paragraphs[start_index],
        end=paragraphs[end_index] if end_index < len(paragraphs) else None,
        evidence_score=sum(evidence),
        evidence_paragraphs=sum(score >= 2 for score in evidence),
        detection_method=detection_method,
    )


def detect_reference_section(
    paragraphs: Sequence[ReferenceParagraph],
) -> ReferenceSection | None:
    """Return a bounded reference interval when native-text evidence agrees."""

    for index, paragraph in enumerate(paragraphs):
        if not is_reference_heading(paragraph.text):
            continue
        evidence = [
            _reference_evidence(item.text)
            for item in paragraphs[index + 1 : index + 9]
        ]
        positive = [score for score in evidence if score >= 2]
        total = sum(evidence)
        if total >= 5 and (len(positive) >= 2 or max(evidence, default=0) >= 5):
            return _make_section(
                paragraphs,
                start_index=index,
                evidence=evidence,
                detection_method="heading_and_entries",
            )

    # Some publishers expose the bibliography entries but omit or fragment the
    # section heading.  Only accept a dense numbered citation run, then require
    # corroborating evidence in the following paragraphs.
    for index, paragraph in enumerate(paragraphs):
        signals = _reference_signals(paragraph.text)
        if not signals.strong_entry_run:
            continue
        following = [
            _reference_evidence(item.text)
            for item in paragraphs[index : index + 7]
        ]
        if sum(following) < 10 or sum(score >= 2 for score in following) < 2:
            continue
        return _make_section(
            paragraphs,
            start_index=index,
            evidence=following,
            detection_method="numbered_entry_run",
        )
    return None


def validate_reference_mode(mode: str) -> str:
    if mode not in REFERENCE_MODES:
        raise ValueError("references_mode must be keep, omit, or separate")
    return mode
