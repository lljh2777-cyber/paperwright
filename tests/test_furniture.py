"""跨页重复页眉/页脚（furniture）剔除策略测试。"""

import unittest

from paperwright.backends.pdfium import _mark_furniture
from paperwright.models import BBox, Element, Page, Provenance

PAGE_H = 800.0
PAGE_W = 612.0
EXCLUDE = "furniture:repeated_page_strip"


def text_element(element_id: str, page_index: int, x: float, y: float, w: float, h: float, value: str) -> Element:
    return Element(
        element_id=element_id,
        kind="text",
        page_index=page_index,
        bbox=BBox(x=x, y=y, width=w, height=h),
        text=value,
        source_object_id=None,
        provenance=Provenance(
            backend="fixture",
            method="fixture",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
        metadata={},
    )


def page(page_index: int, elements: list[Element]) -> Page:
    return Page(
        page_index=page_index,
        width=PAGE_W,
        height=PAGE_H,
        rotation=0,
        elements=tuple(elements),
    )


def marked(pages: list[Page]) -> set[str]:
    return {
        element.element_id
        for page in pages
        for element in page.elements
        if element.metadata.get("markdown_excluded_reason") == EXCLUDE
    }


def make_document(*, header_every_page: bool = True) -> list[Page]:
    """4 页文档：重复页眉/页脚/页码 + 正文 + 底部正文片段 + 单页横幅。"""
    pages: list[Page] = []
    for idx in range(4):
        elements = [
            text_element(f"body-{idx}", idx, 60, 400, 150, 10, "Body paragraph"),
            text_element(f"footer-{idx}", idx, 60, 750, 120, 10, "Journal Footer 2023"),
            text_element(f"pageno-{idx}", idx, 300, 765, 20, 10, f"{42 + idx}"),
            text_element(f"fragment-{idx}", idx, 60, 740, 60, 10, "462–477."),
        ]
        if header_every_page:
            elements.append(text_element(f"header-{idx}", idx, 60, 45, 120, 10, "Running Header"))
        if idx == 3:
            elements.append(text_element("banner", idx, 200, 25, 80, 10, "Bk3"))
        pages.append(page(idx, elements))
    return pages


class FurnitureTests(unittest.TestCase):
    def test_auto_marks_repeated_header_footer_and_page_number(self):
        result = _mark_furniture(make_document(), "auto")
        ids = marked(result)
        self.assertIn("header-0", ids)
        self.assertIn("header-3", ids)
        self.assertIn("footer-0", ids)
        self.assertIn("pageno-0", ids)
        self.assertIn("pageno-3", ids)

    def test_auto_keeps_body_and_mid_band_fragments(self):
        result = _mark_furniture(make_document(), "auto")
        ids = marked(result)
        for idx in range(4):
            self.assertNotIn(f"body-{idx}", ids, "正文不得被剔除")
            self.assertNotIn(f"fragment-{idx}", ids, "底部正文页码区间不得误伤")

    def test_auto_keeps_single_page_banner(self):
        result = _mark_furniture(make_document(), "auto")
        self.assertNotIn("banner", marked(result))

    def test_keep_marks_nothing(self):
        result = _mark_furniture(make_document(), "keep")
        self.assertEqual(marked(result), set())

    def test_strip_removes_short_edge_lines(self):
        result = _mark_furniture(make_document(), "strip")
        self.assertIn("banner", marked(result), "strip 模式应剔除边缘短行")
        self.assertNotIn("body-0", marked(result))

    def test_repeated_reason_preserved_in_metadata(self):
        result = _mark_furniture(make_document(), "auto")
        header = next(
            e for p in result for e in p.elements if e.element_id == "header-0"
        )
        self.assertEqual(header.metadata["markdown_excluded_reason"], EXCLUDE)

    def test_single_page_document_never_marks(self):
        single = [page(0, [text_element("h", 0, 60, 45, 120, 10, "Header")])]
        self.assertEqual(marked(_mark_furniture(single, "auto")), set())


if __name__ == "__main__":
    unittest.main()
