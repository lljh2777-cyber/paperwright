from __future__ import annotations

import hashlib

from paper2md.models import BBox, Element, Page, PhysicalDocument, Provenance


def minimal_document() -> PhysicalDocument:
    source_hash = hashlib.sha256(b"self-generated-bootstrap-fixture").hexdigest()
    element = Element(
        element_id="p0-text-001",
        kind="text",
        page_index=0,
        bbox=BBox(x=72, y=72, width=200, height=24),
        text="Café α bootstrap",
        source_object_id=None,
        provenance=Provenance(
            backend="fixture",
            method="self_generated",
            source_ref="fixture:page:0:text:0",
            confidence=1.0,
        ),
        metadata={"font_unavailable_reason": "self-generated fixture"},
    )
    return PhysicalDocument(
        source_sha256=source_hash,
        backend="fixture",
        backend_version="1",
        pages=(Page(page_index=0, width=612, height=792, rotation=0, elements=(element,)),),
        metadata={"title": "Bootstrap fixture", "rights": "project-authored"},
    )
