import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicOnboardingTests(unittest.TestCase):
    def test_readme_has_complete_windows_and_linux_install_paths(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for required in (
            "git clone https://github.com/lljh2777-cyber/Paper2MD.git",
            "py -3.12 -m venv .venv",
            r".\.venv\Scripts\Activate.ps1",
            "python3 -m venv .venv",
            "source .venv/bin/activate",
            "python -m pip install .",
        ):
            self.assertIn(required, readme)

    def test_readme_has_module_fallback_and_output_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python -m paper2md --help", readme)
        self.assertIn("article.md", readme)
        self.assertIn("physical_document.json", readme)
        self.assertIn("manifest.json", readme)
        self.assertIn("images/", readme)

    def test_declared_python_range_matches_documentation(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertEqual(project["requires-python"], ">=3.10,<3.13")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        quickstart = (ROOT / "docs/QUICKSTART_ALPHA.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Python 3.10、3.11 或 3.12", readme)
        self.assertIn("Python 3.10–3.12", quickstart)

    def test_unverified_platforms_and_license_are_not_overclaimed(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        for platform in ("macOS", "Windows ARM", "Linux ARM"):
            self.assertIn(platform, normalized)
        self.assertIn("尚未验证", readme)
        self.assertIn("尚未添加项目级许可证", readme)
        self.assertNotIn("所有平台均受支持", readme)


if __name__ == "__main__":
    unittest.main()
