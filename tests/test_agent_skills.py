import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "paper2md-install",
    "paper2md-convert",
    "paper2md-contribute",
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
        skill_root = ROOT / "skills" / "paper2md-convert"
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(skill_root.rglob("*.md"))
        )
        for required in (
            "paper2md layout-prepare",
            "paper2md validate-final-layout",
            "paper2md layout-apply",
            "source_element_ids",
            "does not prove semantic correctness",
        ):
            self.assertIn(required, combined)


if __name__ == "__main__":
    unittest.main()
