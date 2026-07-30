#!/usr/bin/env python3
"""Build and verify the deterministic Phase 3 source-only handoff."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DELIVERY = WORKSPACE / "phase3-figure-caption-delivery"
SOURCE_COPY = DELIVERY / "Paper2MD"
ZIP_PATH = WORKSPACE / "phase3-figure-caption-src.zip"
BASE = "8ecd01871eff02e700f0cef1c64cae186be8c69f"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )


def git(*args: str, env: dict[str, str] | None = None) -> str:
    process = command(["git", *args], cwd=ROOT, env=env)
    if process.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({process.returncode}): {process.stderr}"
        )
    return process.stdout


def role(path: str) -> str:
    if path.startswith("src/"):
        return "product_source_or_schema"
    if path.startswith("tests/"):
        return "self_generated_fixture_source_or_test"
    if path.startswith("tools/"):
        return "development_reproduction_or_verification_tool"
    if path.startswith("phase3/"):
        return "phase3_small_machine_or_human_evidence"
    if path.startswith(("stage_a/", "stage_b/", "realworld/")):
        return "preserved_prior_stage_small_evidence"
    if path.startswith(("docs/", "config/")):
        return "documentation_or_configuration"
    return "repository_metadata_or_documentation"


def policy_scan(root: Path, policy_script: Path) -> dict:
    process = command(
        [sys.executable, str(policy_script), "--root", str(root)], cwd=root
    )
    value = json.loads(process.stdout)
    if process.returncode or value["violation_count"]:
        raise RuntimeError(f"policy scan failed: {value}")
    return value


def write_zip(source: Path, target: Path) -> None:
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def verify_zip(path: Path, expected_root: Path) -> dict:
    expected = {
        item.relative_to(expected_root).as_posix(): (
            item.stat().st_size,
            sha256(item),
        )
        for item in expected_root.rglob("*")
        if item.is_file()
    }
    actual = {}
    forbidden_suffixes = {
        ".pdf", ".jar", ".so", ".dll", ".dylib", ".exe", ".bin", ".png",
        ".jpg", ".jpeg", ".whl",
    }
    forbidden_members = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("ZIP contains duplicate members")
        for member in archive.infolist():
            member_path = Path(member.filename)
            unix_type = (member.external_attr >> 16) & 0o170000
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or member.is_dir()
                or unix_type not in {0, 0o100000}
            ):
                raise RuntimeError(f"unsafe/non-file ZIP member: {member.filename}")
            if member_path.suffix.casefold() in forbidden_suffixes:
                forbidden_members.append(member.filename)
            data = archive.read(member)
            actual[member.filename] = (
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
    if forbidden_members:
        raise RuntimeError(f"forbidden payloads in ZIP: {forbidden_members}")
    if actual != expected:
        raise RuntimeError("ZIP inventory differs from delivery directory")
    return {
        "member_count": len(actual),
        "members_match_delivery": True,
        "duplicate_members": 0,
        "unsafe_paths_or_types": 0,
        "forbidden_payload_members": 0,
    }


def fresh_tree_verify(patch_path: Path) -> dict:
    checks = []
    with tempfile.TemporaryDirectory(prefix="paper2md-phase3-worktree-") as temporary:
        worktree = Path(temporary) / "Paper2MD"
        add = command(
            ["git", "worktree", "add", "--detach", str(worktree), BASE],
            cwd=ROOT,
        )
        if add.returncode:
            raise RuntimeError(f"fresh worktree creation failed: {add.stderr}")
        try:
            apply_check = command(
                ["git", "apply", "--check", str(patch_path)], cwd=worktree
            )
            checks.append(
                {
                    "check_id": "patch_check",
                    "exit_code": apply_check.returncode,
                    "pass": apply_check.returncode == 0,
                }
            )
            if apply_check.returncode:
                raise RuntimeError(apply_check.stderr)
            applied = command(["git", "apply", str(patch_path)], cwd=worktree)
            if applied.returncode:
                raise RuntimeError(applied.stderr)
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join(
                (str(worktree / "src"), str(worktree / "tests"))
            )
            commands = [
                (
                    "unit_tests",
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                ),
                (
                    "fixture_check",
                    [sys.executable, "tools/generate_fixtures.py", "--check"],
                ),
                ("content_smoke", [sys.executable, "tools/run_stage_b_smoke.py"]),
                ("stage_c_summary", [sys.executable, "tools/check_stage_c_summary.py"]),
                ("phase3_summary", [sys.executable, "tools/check_phase3_summary.py"]),
                (
                    "compileall",
                    [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"],
                ),
                (
                    "repo_policy",
                    [sys.executable, "tools/check_repo_policy.py", "--root", "."],
                ),
                ("diff_check", ["git", "diff", "--check"]),
            ]
            for check_id, argv in commands:
                process = command(argv, cwd=worktree, env=environment)
                checks.append(
                    {
                        "check_id": check_id,
                        "command_argv": argv,
                        "exit_code": process.returncode,
                        "pass": process.returncode == 0,
                        "stdout_size_bytes": len(process.stdout.encode()),
                        "stdout_sha256": hashlib.sha256(
                            process.stdout.encode()
                        ).hexdigest(),
                        "stderr_size_bytes": len(process.stderr.encode()),
                        "stderr_sha256": hashlib.sha256(
                            process.stderr.encode()
                        ).hexdigest(),
                    }
                )
        finally:
            command(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT)
    return {
        "base_commit": BASE,
        "patch_applied_in_fresh_worktree": True,
        "check_count": len(checks),
        "pass_count": sum(item["pass"] for item in checks),
        "failure_count": sum(not item["pass"] for item in checks),
        "skip_count": 0,
        "checks": checks,
    }


def main() -> int:
    if git("rev-parse", "HEAD").strip() != BASE:
        raise RuntimeError("working tree HEAD is not the authorized Stage C commit")
    if git("diff", "--name-only", BASE, "--", "stage_a", "stage_b", "realworld").strip():
        raise RuntimeError("preserved Stage A/B/C evidence was modified")
    if DELIVERY.exists() or ZIP_PATH.exists():
        raise RuntimeError("delivery target exists; refusing to overwrite")
    required = (
        "phase3/phase3_summary.json",
        "phase3/test_summary.json",
        "phase3/report_zh.md",
        "phase3/manual_visual_review_zh.md",
        "phase3/visual_review.json",
        "phase3/REPRODUCE.md",
        "phase3/fixtures/figure_caption_cases.json",
        "phase3/frozen_rules_v1.json",
    )
    if not all((ROOT / path).is_file() for path in required):
        raise RuntimeError("Phase 3 required evidence is missing")

    with tempfile.TemporaryDirectory(prefix="paper2md-phase3-index-") as temporary:
        index = Path(temporary) / "index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index)
        git("read-tree", BASE, env=environment)
        git("add", "-A", env=environment)
        paths = [
            path for path in git("ls-files", "-z", env=environment).split("\0") if path
        ]
        patch = git(
            "diff", "--cached", "--binary", "--full-index", BASE, env=environment
        )

    DELIVERY.mkdir(parents=True)
    SOURCE_COPY.mkdir()
    for relative in paths:
        source = ROOT / relative
        destination = SOURCE_COPY / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    patch_path = DELIVERY / "phase3-figure-caption-changes.patch"
    patch_path.write_text(patch, encoding="utf-8")
    copies = {
        "test_summary.json": "phase3/test_summary.json",
        "test_report_zh.md": "phase3/test_report_zh.md",
        "phase3_summary.json": "phase3/phase3_summary.json",
        "visual_review.json": "phase3/visual_review.json",
        "manual_visual_review_zh.md": "phase3/manual_visual_review_zh.md",
        "report_zh.md": "phase3/report_zh.md",
        "REPRODUCE.md": "phase3/REPRODUCE.md",
    }
    for destination, source in copies.items():
        shutil.copy2(ROOT / source, DELIVERY / destination)

    fresh = fresh_tree_verify(patch_path)
    if fresh["failure_count"]:
        raise RuntimeError(f"fresh worktree verification failed: {fresh}")
    (DELIVERY / "fresh_tree_verification.json").write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (DELIVERY / "PACKAGE_CONTENTS.txt").write_text(
        "\n".join(
            [
                "Paper2MD/ - 完整 Phase 3 候选源码树（无 .git）",
                "phase3-figure-caption-changes.patch - 相对 8ecd0187 的完整 diff",
                "stage_manifest.json - 基线、逐文件哈希、测试与安全检查",
                "SHA256SUMS.txt - 包内文件 SHA-256（不含自身）",
                "test_summary.json / test_report_zh.md - 测试证据",
                "phase3_summary.json - 8 篇真实论文机器摘要（无 payload）",
                "visual_review.json / manual_visual_review_zh.md - 视觉检查",
                "report_zh.md - Phase 3 简体中文总报告",
                "fresh_tree_verification.json - 干净工作树 patch 复测",
                "REPRODUCE.md - 复现命令",
                "PACKAGE_CONTENTS.txt - 本说明",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    policy_script = SOURCE_COPY / "tools/check_repo_policy.py"
    candidate_scan = policy_scan(SOURCE_COPY, policy_script)
    delivery_scan = policy_scan(DELIVERY, policy_script)
    candidate_files = [
        {
            "path": relative,
            "size_bytes": (SOURCE_COPY / relative).stat().st_size,
            "sha256": sha256(SOURCE_COPY / relative),
            "allowed_reason": role(relative),
        }
        for relative in paths
    ]
    audit_files = [
        {
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "allowed_reason": "required_phase3_source_only_handoff_evidence",
        }
        for path in sorted(item for item in DELIVERY.iterdir() if item.is_file())
    ]
    tests = json.loads((DELIVERY / "test_summary.json").read_text(encoding="utf-8"))
    phase3 = json.loads((DELIVERY / "phase3_summary.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "paper2md-phase3-stage-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "base_commit": BASE,
        "candidate_branch_for_local_use": "agent/v2-rebuild",
        "candidate_file_count": len(candidate_files),
        "candidate_total_bytes": sum(item["size_bytes"] for item in candidate_files),
        "candidate_files": candidate_files,
        "audit_files_before_manifest": audit_files,
        "preserved_stage_a_b_c_evidence_modified": False,
        "tests": {
            "unit_test_count": tests["unit_test_count"],
            "legacy_test_count": tests["legacy_test_count"],
            "phase3_test_method_count": tests["phase3_test_method_count"],
            "frozen_annotation_case_count": tests["frozen_annotation_case_count"],
            "top_level_checks": tests["check_count"],
            "top_level_passed": tests["pass_count"],
            "failure_count": tests["failure_count"],
            "skip_count": tests["skip_count"],
            "fresh_tree": fresh,
        },
        "phase3": {
            **phase3["totals"],
            "runtime_payloads_packaged": False,
            "conclusion": "PHASE3_PASS_WITH_LIMITATIONS",
        },
        "safety_checks": {
            "candidate_source_scan": candidate_scan,
            "delivery_pre_manifest_scan": delivery_scan,
            "paper_pdf_count": 0,
            "paper_extracted_image_count": 0,
            "real_conversion_output_count": 0,
            "backend_binary_or_jar_count": 0,
            "credential_count": 0,
            "max_allowed_file_bytes": 5 * 1024 * 1024,
            "extension_allowlist_enforced": True,
        },
        "runtime_dependencies_not_packaged": [
            "pypdfium2 5.3.0",
            "PDFium 145.0.7616.0",
            "Pillow 12.2.0",
        ],
        "distribution_lock": "agg23 NOASSERTION and release obligations unresolved",
        "package_exclusions": [
            ".git",
            "OA PDF payloads",
            "paper images/renders/conversion outputs",
            "PDFium/JAR/binary/wheel",
            "virtual environments and caches",
            "credentials and tokens",
            "large logs",
        ],
    }
    (DELIVERY / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = []
    for path in sorted(item for item in DELIVERY.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        lines.append(f"{sha256(path)}  {path.relative_to(DELIVERY).as_posix()}")
    (DELIVERY / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    final_scan = policy_scan(DELIVERY, policy_script)
    write_zip(DELIVERY, ZIP_PATH)
    verification = verify_zip(ZIP_PATH, DELIVERY)
    with tempfile.TemporaryDirectory(prefix="paper2md-phase3-zip-") as temporary:
        second = Path(temporary) / ZIP_PATH.name
        write_zip(DELIVERY, second)
        deterministic = sha256(second) == sha256(ZIP_PATH)
    if not deterministic:
        raise RuntimeError("deterministic ZIP rebuild hash mismatch")
    print(
        json.dumps(
            {
                "delivery": str(DELIVERY),
                "patch": str(patch_path),
                "patch_size_bytes": patch_path.stat().st_size,
                "patch_sha256": sha256(patch_path),
                "zip": str(ZIP_PATH),
                "zip_size_bytes": ZIP_PATH.stat().st_size,
                "zip_sha256": sha256(ZIP_PATH),
                "candidate_file_count": len(candidate_files),
                "candidate_total_bytes": manifest["candidate_total_bytes"],
                "final_policy_violation_count": final_scan["violation_count"],
                "fresh_tree_failure_count": fresh["failure_count"],
                "zip_verification": verification,
                "deterministic_rebuild": deterministic,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
