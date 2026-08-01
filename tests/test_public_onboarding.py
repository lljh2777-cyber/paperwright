import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicOnboardingTests(unittest.TestCase):
    def test_package_version_is_consistent_across_metadata_and_docs(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        init_text = (ROOT / "src/paper2md/__init__.py").read_text(
            encoding="utf-8"
        )
        match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(project["version"], match.group(1))
        self.assertIn(
            f'`{project["version"]}`',
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )

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
        self.assertNotIn("所有平台均受支持", readme)

    def test_apache_license_is_declared_and_packaged(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["license-files"], ["LICENSE", "NOTICE"])
        self.assertFalse(
            any(item.startswith("License ::") for item in project["classifiers"])
        )
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        self.assertTrue((ROOT / "NOTICE").is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[Apache License 2.0](LICENSE)", readme)

    def test_current_docs_describe_license_and_manifest_contracts(self):
        support = (ROOT / "docs/SUPPORT_MATRIX.md").read_text(
            encoding="utf-8"
        )
        release = (ROOT / "docs/ALPHA_RC_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(
            encoding="utf-8"
        )
        for document in (support, release):
            self.assertIn("Apache License 2.0", document)
            self.assertNotIn("项目许可证仍为 `NOASSERTION`", document)
        self.assertIn("hybrid manifest | v0.7", architecture)
        self.assertIn("继续接受旧 v0.6", architecture)


if __name__ == "__main__":
    unittest.main()
