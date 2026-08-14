import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "paperwright-install",
    "paperwright-convert",
    "paperwright-contribute",
    "paperwright-agent-workflow",
)


class AgentSkillTests(unittest.TestCase):
    def test_distributed_skills_have_valid_identity_and_no_placeholders(self):
        for name in SKILLS:
            root = ROOT / "skills" / name
            skill = (root / "SKILL.md").read_text(encoding="utf-8")
            metadata = (root / "agents" / "openai.yaml").read_text(
                encoding="utf-8"
            )
            self.assertRegex(skill, rf"(?m)^name: {re.escape(name)}$")
            self.assertRegex(skill, r"(?m)^description: \S.+$")
            self.assertNotIn("TODO", skill)
            self.assertIn(f"${name}", metadata)
            self.assertTrue((root / "references").is_dir())

    def test_readme_exposes_all_skills(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in SKILLS:
            self.assertIn(f"skills/{name}/SKILL.md", readme)
            self.assertIn(f"${name}", readme)

    def test_conversion_skill_preserves_review_boundary(self):
        skill_root = ROOT / "skills" / "paperwright-convert"
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(skill_root.rglob("*.md"))
        )
        for required in (
            "paperwright layout-prepare",
            "paperwright validate-final-layout",
            "paperwright layout-apply",
            "source_element_ids",
            "does not prove semantic correctness",
        ):
            self.assertIn(required, combined)

    def test_skills_require_staged_user_decisions(self):
        convert_root = ROOT / "skills" / "paperwright-convert"
        convert = (convert_root / "SKILL.md").read_text(encoding="utf-8")
        option_guide = (
            convert_root / "references" / "options-and-questions.md"
        ).read_text(encoding="utf-8")
        install = (
            ROOT / "skills" / "paperwright-install" / "SKILL.md"
        ).read_text(encoding="utf-8")
        contribute = (
            ROOT / "skills" / "paperwright-contribute" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Ask before running", convert)
        self.assertIn("no more than three", convert)
        self.assertIn("options-and-questions.md", convert)
        for required in (
            "Workflow",
            "Output destination",
            "Extraction profile",
            "Final package policy",
            "Failure handling",
            "Content ROI confirmation is a separate mandatory checkpoint",
        ):
            self.assertIn(required, option_guide)
        self.assertIn("Confirm installation choices", install)
        self.assertIn("Confirm contribution scope", contribute)

    def test_agent_workflow_separates_visual_and_text_reviewers(self):
        root = ROOT / "skills" / "paperwright-agent-workflow"
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.md"))
        )
        for required in (
            "paperwright text-prepare",
            "paperwright validate-text-review",
            "paperwright text-apply",
            "paperwright text-package",
            "paperwright validate-text-package",
            "Text reviewer input: text task JSON only",
            "Do not merge, delete",
        ):
            self.assertIn(required, combined)


if __name__ == "__main__":
    unittest.main()
