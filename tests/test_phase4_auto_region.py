from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from paperwright.api import PaperWright
from paperwright.backends import BackendRegistry
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.cli import main as cli_main
from paperwright.config import PaperWrightConfig, RegionRenderPolicy
from paperwright.exceptions import ConfigurationError
from paperwright.manifest import validate_manifest

from pdf_fixture_factory import create_auto_region_fixture


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class Phase4AutoRegionTests(unittest.TestCase):
    @staticmethod
    def _registry() -> BackendRegistry:
        value = BackendRegistry()
        value.register("pdfium", PDFiumBackend())
        return value

    def _convert(
        self,
        case: str,
        *,
        policy: RegionRenderPolicy | None = None,
        rotation: int = 0,
    ) -> tuple[Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / f"{case}.pdf"
        create_auto_region_fixture(source, case, rotation=rotation)
        output = root / "output"
        result = PaperWright(
            config=PaperWrightConfig(
                region_render=policy or RegionRenderPolicy(mode="auto")
            ),
            registry=self._registry(),
        ).convert(source, output)
        return output, result.manifest

    def test_policy_modes_are_explicit_and_default_off(self):
        self.assertEqual(RegionRenderPolicy().effective_mode, "off")
        self.assertEqual(
            RegionRenderPolicy(enabled=True, page_indices=(0,)).effective_mode,
            "explicit",
        )
        RegionRenderPolicy(mode="auto").validate()
        with self.assertRaises(ConfigurationError):
            RegionRenderPolicy(mode="auto", page_indices=(0,)).validate()
        with self.assertRaises(ConfigurationError):
            RegionRenderPolicy(mode="explicit").validate()

    def test_complete_single_bitmap_is_not_promoted(self):
        _, manifest = self._convert("single_bitmap")
        self.assertEqual(manifest["manifest_version"], "paperwright-manifest-v0.5")
        self.assertEqual(manifest["region_render_policy"]["mode"], "auto")
        self.assertFalse(
            any(
                figure["extraction_mode"] == "region-rendered"
                for figure in manifest["figures"]
            )
        )
        self.assertEqual(
            manifest["figures"][0]["region_render"]["status"],
            "not_requested",
        )

    def test_pure_vector_is_rejected_without_fabricated_native_group(self):
        _, manifest = self._convert("pure_vector")
        self.assertEqual(manifest["figures"], [])
        reasons = {
            item["reason"] for item in manifest["figure_rejections"]
        }
        self.assertIn(
            "pure_vector_without_native_figure_group_not_auto_rendered",
            reasons,
        )

    def test_mixed_bitmap_vector_is_rendered_and_traceable(self):
        output, manifest = self._convert("mixed")
        rendered = [
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        ]
        self.assertEqual(len(rendered), 1)
        figure = rendered[0]
        self.assertEqual(figure["region_render"]["status"], "rendered")
        self.assertTrue(figure["native_asset"]["retained_for_provenance"])
        self.assertTrue(figure["vector_evidence"]["rendered_into_asset"])
        self.assertEqual(
            hashlib.sha256(
                (output / figure["asset"]["path"]).read_bytes()
            ).hexdigest(),
            figure["asset"]["sha256"],
        )
        validate_manifest(manifest)

    def test_multipanel_group_remains_one_region_with_two_native_members(self):
        _, manifest = self._convert("multi_panel")
        rendered = [
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        ]
        self.assertEqual(len(rendered), 1)
        self.assertEqual(len(rendered[0]["member_element_ids"]), 2)
        self.assertEqual(rendered[0]["native_asset"]["mode"], "grouped")

    def test_adjacent_column_figures_do_not_share_caption(self):
        _, manifest = self._convert("adjacent")
        rendered = [
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        ]
        self.assertEqual(len(rendered), 2)
        caption_ids = [item["caption"]["caption_id"] for item in rendered]
        self.assertEqual(len(set(caption_ids)), 2)
        self.assertTrue(
            all(item["caption"]["status"] == "matched" for item in rendered)
        )

    def test_caption_ambiguity_is_rejected(self):
        _, manifest = self._convert("ambiguous")
        reasons = {
            item["reason"]
            for item in manifest["figure_rejections"]
            if item.get("evidence_status") == "region_render_rejected"
        }
        self.assertIn("caption_ambiguous", reasons)
        self.assertFalse(
            any(
                item["extraction_mode"] == "region-rendered"
                for item in manifest["figures"]
            )
        )

    def test_cross_page_continued_is_rejected(self):
        _, manifest = self._convert("continued")
        reasons = {
            item["reason"] for item in manifest["figure_rejections"]
        }
        self.assertIn(
            "cross_page_figure_continuation_explicitly_detected", reasons
        )
        self.assertFalse(
            any(
                item["extraction_mode"] == "region-rendered"
                for item in manifest["figures"]
            )
        )

    def test_near_full_fixture_is_conservatively_rejected(self):
        _, manifest = self._convert("near_full")
        region = manifest["figures"][0]["region_render"]
        self.assertEqual(region["status"], "rejected")
        self.assertIn(
            region["reason"],
            {
                "near_full_page_region_rejected",
                "insufficient_deterministic_region_boundary",
            },
        )

    def test_body_text_intrusion_is_rejected(self):
        _, manifest = self._convert("body_intrusion")
        region = manifest["figures"][0]["region_render"]
        self.assertEqual(region["status"], "rejected")
        self.assertEqual(region["reason"], "suspected_body_text_intrusion")
        self.assertIn(
            "region_render_rejected:suspected_body_text_intrusion",
            manifest["figures"][0]["degraded_reasons"],
        )

    def test_caption_span_mismatch_rejects_partial_multi_panel_crop(self):
        _, manifest = self._convert("caption_span_mismatch")
        region = manifest["figures"][0]["region_render"]
        self.assertEqual(region["status"], "rejected")
        self.assertEqual(
            region["reason"],
            "candidate_does_not_cover_caption_horizontal_span",
        )
        self.assertTrue(
            manifest["figures"][0]["native_asset"][
                "retained_for_provenance"
            ]
        )

    def test_rotated_fixture_never_silently_misrenders(self):
        _, manifest = self._convert("rotated", rotation=90)
        rendered = [
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        ]
        if rendered:
            self.assertEqual(rendered[0]["region_render"]["rotation"], 90)
        else:
            self.assertTrue(
                any(
                    item["region_render"]["status"] == "rejected"
                    for item in manifest["figures"]
                )
            )

    def test_candidate_limit_rejects_excess_without_dropping_native(self):
        _, manifest = self._convert(
            "adjacent",
            policy=RegionRenderPolicy(
                mode="auto", max_candidates_per_document=1
            ),
        )
        rendered = [
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        ]
        self.assertEqual(len(rendered), 1)
        self.assertIn(
            "document_candidate_limit_exceeded",
            {item["reason"] for item in manifest["figure_rejections"]},
        )
        self.assertTrue(
            all(item["native_asset"]["retained_for_provenance"] for item in manifest["figures"])
        )

    def test_markdown_region_asset_is_immediately_before_caption(self):
        output, manifest = self._convert("mixed")
        figure = next(
            item
            for item in manifest["figures"]
            if item["extraction_mode"] == "region-rendered"
        )
        article = (output / "article.md").read_text(encoding="utf-8")
        self.assertLess(
            article.index(f"]({figure['asset']['path']})"),
            article.index("Figure 1."),
        )
        self.assertEqual(
            figure["markdown_placement"], "immediately-before-caption"
        )

    def test_auto_mode_is_byte_deterministic(self):
        first, _ = self._convert("mixed")
        second, _ = self._convert("mixed")
        self.assertEqual(_tree(first), _tree(second))

    def test_explicit_legacy_alias_remains_supported(self):
        _, manifest = self._convert(
            "mixed",
            policy=RegionRenderPolicy(enabled=True, page_indices=(0,)),
        )
        self.assertEqual(manifest["region_render_policy"]["mode"], "explicit")
        self.assertEqual(
            sum(
                item["extraction_mode"] == "region-rendered"
                for item in manifest["figures"]
            ),
            1,
        )

    def test_cli_auto_mode_produces_valid_v05_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "mixed.pdf"
            output = root / "output"
            create_auto_region_fixture(source, "mixed")
            exit_code = cli_main(
                [
                    "convert",
                    str(source),
                    str(output),
                    "--region-render-mode",
                    "auto",
                    "--region-render-max-candidates",
                    "2",
                ]
            )
            self.assertEqual(exit_code, 0)
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            self.assertEqual(
                manifest["region_render_policy"]["max_candidates_per_document"],
                2,
            )


if __name__ == "__main__":
    unittest.main()
