#!/usr/bin/env python3
"""Build and verify the deterministic Stage C source-only handoff."""

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
DELIVERY = WORKSPACE / "v2-realworld-delivery"
SOURCE_COPY = DELIVERY / "Paper2MD"
ZIP_PATH = WORKSPACE / "v2-realworld-src.zip"
BASE = "0897f3ca82b74468ece7aa65d6e331416c4afd96"
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
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
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
    if path.startswith(("stage_a/", "stage_b/")):
        return "preserved_prior_stage_small_evidence"
    if path.startswith("realworld/"):
        return "stage_c_source_manifest_summary_or_small_evidence"
    if path.startswith(("docs/", "config/")):
        return "documentation_or_configuration"
    return "repository_metadata_or_documentation"


def policy_scan(root: Path, policy_script: Path) -> dict:
    process = command(
        [sys.executable, str(policy_script), "--root", str(root)],
        cwd=root,
    )
    value = json.loads(process.stdout)
    if process.returncode or value["violation_count"]:
        raise RuntimeError(f"policy scan failed: {value}")
    return value


def write_zip(source: Path, target: Path) -> None:
    files = sorted(item for item in source.rglob("*") if item.is_file())
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def verify_zip(path: Path, expected_root: Path) -> dict:
    expected = {
        item.relative_to(expected_root).as_posix(): (item.stat().st_size, sha256(item))
        for item in expected_root.rglob("*")
        if item.is_file()
    }
    actual = {}
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
            data = archive.read(member)
            actual[member.filename] = (
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
    if actual != expected:
        raise RuntimeError("ZIP inventory differs from delivery directory")
    return {
        "member_count": len(actual),
        "members_match_delivery": True,
        "duplicate_members": 0,
        "unsafe_paths_or_types": 0,
    }


def fresh_tree_verify(patch_path: Path) -> dict:
    checks = []
    with tempfile.TemporaryDirectory(prefix="paper2md-stage-c-worktree-") as temporary:
        worktree = Path(temporary) / "Paper2MD"
        add = command(
            ["git", "worktree", "add", "--detach", str(worktree), BASE],
            cwd=ROOT,
        )
        if add.returncode:
            raise RuntimeError(f"fresh worktree creation failed: {add.stderr}")
        try:
            apply_check = command(
                ["git", "apply", "--check", str(patch_path)],
                cwd=worktree,
            )
            checks.append(
                {
                    "check_id": "patch_check",
                    "exit_code": apply_check.returncode,
                    "pass": apply_check.returncode == 0,
                    "stdout_sha256": hashlib.sha256(
                        apply_check.stdout.encode()
                    ).hexdigest(),
                    "stderr_sha256": hashlib.sha256(
                        apply_check.stderr.encode()
                    ).hexdigest(),
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
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-v",
                    ],
                ),
                (
                    "fixture_check",
                    [sys.executable, "tools/generate_fixtures.py", "--check"],
                ),
                (
                    "content_smoke",
                    [sys.executable, "tools/run_stage_b_smoke.py"],
                ),
                (
                    "stage_c_summary",
                    [sys.executable, "tools/check_stage_c_summary.py"],
                ),
                (
                    "compileall",
                    [
                        sys.executable,
                        "-m",
                        "compileall",
                        "-q",
                        "src",
                        "tests",
                        "tools",
                    ],
                ),
                (
                    "repo_policy",
                    [
                        sys.executable,
                        "tools/check_repo_policy.py",
                        "--root",
                        ".",
                    ],
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
            command(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=ROOT,
            )
    return {
        "base_commit": BASE,
        "patch_applied_in_fresh_worktree": True,
        "check_count": len(checks),
        "pass_count": sum(bool(item["pass"]) for item in checks),
        "failure_count": sum(not bool(item["pass"]) for item in checks),
        "skip_count": 0,
        "checks": checks,
    }


def main() -> int:
    if git("rev-parse", "HEAD").strip() != BASE:
        raise RuntimeError("working tree HEAD is not authorized Stage B commit")
    for prior in ("stage_a", "stage_b"):
        if git("diff", "--name-only", BASE, "--", prior).strip():
            raise RuntimeError(f"preserved {prior} evidence was modified")
    if DELIVERY.exists() or ZIP_PATH.exists():
        raise RuntimeError("delivery target exists; refusing to overwrite")
    required = (
        "realworld/oa_sources.json",
        "realworld/realworld_summary.json",
        "realworld/test_summary.json",
        "realworld/test_report_zh.md",
        "realworld/report_zh.md",
        "realworld/manual_review_zh.md",
        "realworld/REPRODUCE.md",
    )
    if not all((ROOT / path).is_file() for path in required):
        raise RuntimeError("Stage C required evidence is missing")

    with tempfile.TemporaryDirectory(prefix="paper2md-stage-c-index-") as temporary:
        index = Path(temporary) / "index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index)
        git("read-tree", BASE, env=environment)
        git("add", "-A", env=environment)
        paths = [
            path for path in git("ls-files", "-z", env=environment).split("\0") if path
        ]
        patch = git(
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            BASE,
            env=environment,
        )

    DELIVERY.mkdir(parents=True)
    SOURCE_COPY.mkdir()
    for relative in paths:
        source = ROOT / relative
        destination = SOURCE_COPY / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    patch_path = DELIVERY / "v2-realworld-changes.patch"
    patch_path.write_text(patch, encoding="utf-8")
    copies = {
        "test_summary.json": "realworld/test_summary.json",
        "test_report_zh.md": "realworld/test_report_zh.md",
        "realworld_summary.json": "realworld/realworld_summary.json",
        "oa_sources.json": "realworld/oa_sources.json",
        "manual_review_zh.md": "realworld/manual_review_zh.md",
        "REPRODUCE.md": "realworld/REPRODUCE.md",
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
                "Paper2MD/ - 完整 Stage C 候选源码树（无 .git）",
                "v2-realworld-changes.patch - 相对 0897f3c 的完整可应用 diff",
                "stage_manifest.json - 基线、文件哈希、测试与安全检查",
                "SHA256SUMS.txt - 包内文件 SHA-256（不含自身）",
                "test_summary.json / test_report_zh.md - 测试证据",
                "realworld_summary.json - 8篇真实论文机器摘要（无payload）",
                "oa_sources.json - OA来源/许可/hash/页数清单",
                "manual_review_zh.md - 简体中文逐篇人工检查",
                "fresh_tree_verification.json - 干净工作树复测",
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
            "allowed_reason": "required_stage_c_source_only_handoff_evidence",
        }
        for path in sorted(item for item in DELIVERY.iterdir() if item.is_file())
    ]
    tests = json.loads((DELIVERY / "test_summary.json").read_text(encoding="utf-8"))
    realworld = json.loads(
        (DELIVERY / "realworld_summary.json").read_text(encoding="utf-8")
    )
    manifest = {
        "schema_version": "paper2md-v2-realworld-stage-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "base_commit": BASE,
        "candidate_branch_for_local_use": "agent/v2-rebuild",
        "new_rw2_corpus_not_historical_checkpoint_recovery": True,
        "preserved_stage_a_b_evidence_modified": False,
        "candidate_file_count": len(candidate_files),
        "candidate_total_bytes": sum(item["size_bytes"] for item in candidate_files),
        "candidate_files": candidate_files,
        "audit_files_before_manifest": audit_files,
        "tests": {
            "unit_test_count": tests["unit_test_count"],
            "unit_test_passed": tests["unit_test_passed"],
            "content_assertion_count": tests["content_assertion_count"],
            "content_assertion_passed": tests["content_assertion_passed"],
            "top_level_checks": tests["check_count"],
            "top_level_passed": tests["pass_count"],
            "failure_count": tests["failure_count"],
            "skip_count": tests["skip_count"],
            "fresh_tree": fresh,
        },
        "realworld": {
            "paper_count": 8,
            "page_count": 138,
            "success_count": realworld["totals"]["success_count"],
            "title_exact_normalized": realworld["totals"][
                "title_exact_normalized"
            ],
            "valid_images": realworld["totals"]["valid_image_count"],
            "degraded_table_pages": realworld["totals"][
                "degraded_table_page_count"
            ],
            "determinism_pass": realworld["totals"]["determinism_pass_count"],
            "runtime_payloads_packaged": False,
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
            "large interactive logs",
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
    with tempfile.TemporaryDirectory(prefix="paper2md-stage-c-zip-") as temporary:
        second = Path(temporary) / ZIP_PATH.name
        write_zip(DELIVERY, second)
        deterministic = sha256(second) == sha256(ZIP_PATH)
    if not deterministic:
        raise RuntimeError("deterministic ZIP rebuild hash mismatch")
    result = {
        "delivery": str(DELIVERY),
        "zip": str(ZIP_PATH),
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "candidate_file_count": len(candidate_files),
        "candidate_total_bytes": manifest["candidate_total_bytes"],
        "final_policy_violation_count": final_scan["violation_count"],
        "fresh_tree_failure_count": fresh["failure_count"],
        "zip_verification": verification,
        "deterministic_rebuild": deterministic,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
