from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from paperwright.article_model import (
    ARTICLE_MODEL_CONTRACT_VERSION,
    article_model_to_reader,
    canonical_article_model_json,
    render_article_markdown,
    validate_article_model,
)
from paperwright.article_tree import (
    ARTICLE_TREE_CONTRACT_VERSION,
    article_tree_to_article_model,
    canonical_final_article_tree_json,
    validate_final_article_tree,
)
from paperwright.cli import main
from paperwright.exceptions import ContractValidationError
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.reader import (
    READER_CONTRACT_VERSION,
    canonical_reader_json,
    compile_reviewed_article,
    validate_reader_index,
)


def _element(
    element_id: str,
    kind: str,
    bbox: BBox,
    text: str | None = None,
) -> Element:
    return Element(
        element_id=element_id,
        kind=kind,
        page_index=0,
        bbox=bbox,
        text=text,
        provenance=Provenance(
            backend="fixture",
            method="self-generated",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
    )


class ReaderContractTests(unittest.TestCase):
    def _fixture(self):
        title = _element(
            "title-1", "text", BBox(50, 40, 300, 20), "Reader Fixture"
        )
        body = _element(
            "body-1", "text", BBox(50, 100, 240, 30), "Body cites Fig. 1."
        )
        image = _element(
            "image-1", "image", BBox(50, 180, 300, 180)
        )
        caption = _element(
            "caption-1",
            "text",
            BBox(50, 370, 300, 30),
            "Figure 1. Stable binding.",
        )
        source_sha256 = hashlib.sha256(b"reader-fixture").hexdigest()
        document = PhysicalDocument(
            source_sha256=source_sha256,
            backend="fixture",
            backend_version="1",
            pages=(
                Page(
                    page_index=0,
                    width=600,
                    height=800,
                    rotation=0,
                    elements=(title, body, image, caption),
                ),
            ),
            metadata={"title": "Reader Fixture"},
        )

        def digest(*ids: str) -> str:
            return hashlib.sha256("\0".join(ids).encode("utf-8")).hexdigest()

        body_digest = digest("body-1")
        image_digest = digest("image-1")
        caption_digest = digest("caption-1")
        lines = [
            "# Reader Fixture",
            "",
            "<!-- page: 1 -->",
            "",
            "<!-- layout-region: body; role: body; page: 1; "
            f"element-count: 1; elements-sha256: {body_digest}; "
            "provenance-ref: page/1/region/body/paragraph/0 -->",
            "Body cites Fig. 1.",
            "",
            "<!-- layout-region: visual; role: figure; page: 1; "
            f"element-count: 1; elements-sha256: {image_digest}; "
            "provenance-ref: page/1/region/visual -->",
            "![Figure 1](images/figure-0001.png)",
            "",
            "<!-- caption-for: page: 1; region: visual; method: fixture -->",
            "<!-- layout-region: caption; role: caption; page: 1; "
            f"element-count: 1; elements-sha256: {caption_digest}; "
            "provenance-ref: page/1/region/caption/paragraph/0 -->",
            "**Figure 1.** Stable binding.",
            "",
        ]
        provenance = [
            {
                "page_index": 0,
                "regions": [
                    {
                        "region_id": "body",
                        "bbox": {
                            "x": 0.08,
                            "y": 0.12,
                            "width": 0.40,
                            "height": 0.05,
                        },
                        "paragraphs": [
                            {
                                "paragraph_index": 0,
                                "source_element_ids": ["body-1"],
                            }
                        ],
                    },
                    {
                        "region_id": "visual",
                        "bbox": {
                            "x": 0.08,
                            "y": 0.22,
                            "width": 0.50,
                            "height": 0.23,
                        },
                    },
                    {
                        "region_id": "caption",
                        "bbox": {
                            "x": 0.08,
                            "y": 0.46,
                            "width": 0.50,
                            "height": 0.04,
                        },
                        "paragraphs": [
                            {
                                "paragraph_index": 0,
                                "source_element_ids": ["caption-1"],
                            }
                        ],
                    },
                ],
            }
        ]
        image_data = b"project-authored-reader-image"
        images = [
            {
                "region_id": "visual",
                "role": "figure",
                "page": 1,
                "path": "images/figure-0001.png",
                "bbox": {
                    "x": 50,
                    "y": 180,
                    "width": 300,
                    "height": 180,
                },
                "width_px": 600,
                "height_px": 360,
                "size_bytes": len(image_data),
                "sha256": hashlib.sha256(image_data).hexdigest(),
                "renderer_version": "fixture",
                "source_pdf_sha256": source_sha256,
                "ocr_used": False,
                "caption_binding": {
                    "caption_page": 1,
                    "caption_region_id": "caption",
                    "visual_page": 1,
                    "visual_region_id": "visual",
                    "method": "fixture",
                    "score": 1.0,
                },
            }
        ]
        return document, lines, provenance, images, image_data

    def _compile(self):
        document, lines, provenance, images, image_data = self._fixture()
        compilation = compile_reviewed_article(
            lines,
            document=document,
            title_element_ids=("title-1",),
            provenance_pages=provenance,
            image_records=images,
        )
        reader = compilation.reader_index(
            source_sha256=document.source_sha256
        )
        return compilation, reader, provenance, images, image_data

    def test_compilation_is_deterministic_and_removes_private_traces(self):
        first = self._compile()
        second = self._compile()
        self.assertEqual(first[0].markdown_text(), second[0].markdown_text())
        self.assertEqual(
            canonical_reader_json(first[1]),
            canonical_reader_json(second[1]),
        )
        article = first[0].markdown_text()
        self.assertIn("<!-- pwwd:block", article)
        self.assertIn("<!-- pwwd:slot", article)
        self.assertNotIn("<!-- layout-region:", article)
        self.assertNotIn("<!-- page:", article)

    def test_article_tree_is_canonical_source_for_article_model_and_reader(self):
        compilation, reader, _, _, _ = self._compile()
        tree = compilation.article_tree(source_sha256=reader["source_sha256"])
        validate_final_article_tree(tree)
        self.assertEqual(
            tree["contract_version"],
            ARTICLE_TREE_CONTRACT_VERSION,
        )
        self.assertEqual(tree["summary"]["generated_text_count"], 0)
        model = article_tree_to_article_model(tree)
        self.assertEqual(
            model["contract_version"],
            ARTICLE_MODEL_CONTRACT_VERSION,
        )
        self.assertEqual(
            render_article_markdown(model),
            compilation.markdown_text(),
        )
        self.assertEqual(article_model_to_reader(model), reader)
        self.assertEqual(
            canonical_article_model_json(model),
            canonical_article_model_json(
                compilation.article_model(
                    source_sha256=reader["source_sha256"]
                )
            ),
        )

    def test_final_article_tree_is_deterministic_and_rejects_drift(self):
        compilation, reader, _, _, _ = self._compile()
        first = compilation.article_tree(source_sha256=reader["source_sha256"])
        second = compilation.article_tree(source_sha256=reader["source_sha256"])
        self.assertEqual(
            canonical_final_article_tree_json(first),
            canonical_final_article_tree_json(second),
        )
        tampered = deepcopy(first)
        tampered["nodes"][1]["order"] = 9
        with self.assertRaisesRegex(ContractValidationError, "block"):
            validate_final_article_tree(tampered)
        wrong_source = "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "输入身份"):
            validate_final_article_tree(
                first,
                expected_source_sha256=wrong_source,
            )

    def test_article_model_rejects_multiline_blocks_and_order_gaps(self):
        compilation, reader, _, _, _ = self._compile()
        model = compilation.article_model(source_sha256=reader["source_sha256"])
        multiline = deepcopy(model)
        multiline["blocks"][1]["markdown"] += "\nunauthorized"
        with self.assertRaisesRegex(ContractValidationError, "单行"):
            validate_article_model(multiline)
        order_gap = deepcopy(model)
        order_gap["blocks"][1]["order"] = 9
        with self.assertRaisesRegex(ContractValidationError, "order"):
            validate_article_model(order_gap)
        broken_title = deepcopy(model)
        broken_title["blocks"][0]["markdown"] = "Reader Fixture"
        with self.assertRaisesRegex(ContractValidationError, "H1"):
            validate_article_model(broken_title)
        wrong_image = deepcopy(model)
        slot = next(
            item for item in wrong_image["blocks"]
            if item["kind"] == "visual_slot"
        )
        slot["markdown"] = "![Figure 1](images/wrong.png)"
        with self.assertRaisesRegex(ContractValidationError, "asset path"):
            validate_article_model(wrong_image)

    def test_source_backed_title_id_does_not_depend_on_rendered_text(self):
        document, lines, provenance, images, _ = self._fixture()
        first = compile_reviewed_article(
            lines,
            document=document,
            title_element_ids=("title-1",),
            provenance_pages=deepcopy(provenance),
            image_records=deepcopy(images),
        )
        edited_lines = list(lines)
        edited_lines[0] = "# Reader Fixture (display edit)"
        second = compile_reviewed_article(
            edited_lines,
            document=document,
            title_element_ids=("title-1",),
            provenance_pages=deepcopy(provenance),
            image_records=deepcopy(images),
        )
        self.assertEqual(first.blocks[0]["id"], second.blocks[0]["id"])
        self.assertNotEqual(
            first.blocks[0]["fingerprint"], second.blocks[0]["fingerprint"]
        )

    def test_compilation_rejects_untraced_semantic_content(self):
        document, lines, provenance, images, _ = self._fixture()
        lines.append("Untraced content must not enter the public article.")
        with self.assertRaisesRegex(ContractValidationError, "缺少 source trace"):
            compile_reviewed_article(
                lines,
                document=document,
                title_element_ids=("title-1",),
                provenance_pages=provenance,
                image_records=images,
            )

    def test_reader_links_slot_asset_and_caption_without_copying_caption(self):
        _, reader, provenance, images, _ = self._compile()
        self.assertEqual(reader["contract_version"], READER_CONTRACT_VERSION)
        self.assertEqual(len(reader["assets"]), 1)
        asset = reader["assets"][0]
        self.assertEqual(asset["display_label"], "Figure 1")
        self.assertIsNotNone(asset["caption_block_id"])
        self.assertTrue(asset["placement_block_id"].startswith("slot_"))
        self.assertNotIn("caption", asset)
        self.assertEqual(
            {item["type"] for item in reader["relations"]},
            {"places", "caption-of"},
        )
        self.assertEqual(images[0]["reader_asset_id"], asset["id"])
        caption_paragraph = provenance[0]["regions"][2]["paragraphs"][0]
        self.assertEqual(
            caption_paragraph["article_block_id"],
            asset["caption_block_id"],
        )

    def test_article_hash_and_anchor_set_are_strict(self):
        compilation, reader, _, _, _ = self._compile()
        with self.assertRaisesRegex(ContractValidationError, "article hash"):
            validate_reader_index(
                reader,
                article_text=compilation.markdown_text() + "edited\n",
            )
        duplicate = compilation.markdown_text() + compilation.markdown_lines[0] + "\n"
        changed = deepcopy(reader)
        changed["article"]["sha256"] = hashlib.sha256(
            duplicate.encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ContractValidationError, "anchor 重复"):
            validate_reader_index(changed, article_text=duplicate)

    def test_block_fingerprint_and_relation_semantics_are_strict(self):
        compilation, reader, _, _, _ = self._compile()
        bad_fingerprint = deepcopy(reader)
        bad_fingerprint["blocks"][1]["fingerprint"][
            "visible_text_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "指纹不一致"):
            validate_reader_index(
                bad_fingerprint,
                article_text=compilation.markdown_text(),
            )

        bad_relation = deepcopy(reader)
        places = next(
            item
            for item in bad_relation["relations"]
            if item["type"] == "places"
        )
        places["label"] = "not allowed"
        with self.assertRaisesRegex(ContractValidationError, "places"):
            validate_reader_index(bad_relation)

    def test_malformed_top_level_is_a_contract_error(self):
        with self.assertRaises(ContractValidationError):
            validate_reader_index([])  # type: ignore[arg-type]
        _, reader, _, _, _ = self._compile()
        reader["blocks"][0]["source_spans"][0]["bbox"]["x"] = float("nan")
        with self.assertRaisesRegex(ContractValidationError, "bbox"):
            validate_reader_index(reader)

    def test_validate_reader_cli_checks_complete_package(self):
        compilation, reader, _, _, image_data = self._compile()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "images").mkdir()
            (root / "_paperwright").mkdir()
            (root / "article.md").write_text(
                compilation.markdown_text(), encoding="utf-8", newline="\n"
            )
            (root / "images/figure-0001.png").write_bytes(image_data)
            reader_path = root / "_paperwright/reader.json"
            reader_path.write_text(
                canonical_reader_json(reader),
                encoding="utf-8",
                newline="\n",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["validate-reader", str(reader_path)])
            summary = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(summary["status"], "valid")
            self.assertEqual(summary["block_count"], 4)
            self.assertEqual(summary["asset_count"], 1)

    def test_validate_article_model_cli_checks_both_projections(self):
        compilation, reader, _, _, image_data = self._compile()
        model = compilation.article_model(source_sha256=reader["source_sha256"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "images").mkdir()
            (root / "_paperwright").mkdir()
            (root / "article.md").write_text(
                render_article_markdown(model),
                encoding="utf-8",
                newline="\n",
            )
            (root / "images/figure-0001.png").write_bytes(image_data)
            (root / "_paperwright/reader.json").write_text(
                canonical_reader_json(article_model_to_reader(model)),
                encoding="utf-8",
                newline="\n",
            )
            model_path = root / "_paperwright/article-model.json"
            model_path.write_text(
                canonical_article_model_json(model),
                encoding="utf-8",
                newline="\n",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["validate-article-model", str(model_path)])
            summary = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(summary["status"], "valid")
            self.assertEqual(summary["block_count"], 4)
            self.assertEqual(summary["asset_count"], 1)

            (root / "article.md").write_text(
                render_article_markdown(model) + "changed\n",
                encoding="utf-8",
                newline="\n",
            )
            self.assertNotEqual(
                main(["validate-article-model", str(model_path)]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
