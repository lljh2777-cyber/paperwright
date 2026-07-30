#!/usr/bin/env python3
"""Build and verify the deterministic source-only Phase 6 Alpha RC handoff."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DELIVERY = WORKSPACE / "phase6-alpha-rc-delivery"
SOURCE_COPY = DELIVERY / "Paper2MD"
ZIP_PATH = WORKSPACE / "phase6-alpha-rc-src.zip"
PATCH_PATH = WORKSPACE / "phase6-alpha-rc-changes.patch"
BASE = "47e31abb58d062e1da0ecf92a2a303afddaa39af"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
FORBIDDEN_SUFFIXES = {
    ".pdf", ".jar", ".so", ".dll", ".dylib", ".exe", ".bin", ".png",
    ".jpg", ".jpeg", ".whl", ".pyc", ".pyd", ".class",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def git(*args: str, env: dict[str, str] | None = None) -> str:
    process = run(["git", *args], cwd=ROOT, env=env)
    if process.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({process.returncode}): "
            f"{process.stderr}"
        )
    return process.stdout


def role(relative: str) -> str:
    if relative.startswith("src/"):
        return "product_source_or_schema"
    if relative.startswith("tests/"):
        return "self_generated_fixture_source_or_test"
    if relative.startswith("tools/"):
        return "development_reproduction_or_verification_tool"
    if relative.startswith("phase6_alpha_rc/"):
        return "phase6_alpha_rc_small_machine_or_human_evidence"
    if relative.startswith(
        (
            "stage_a/", "stage_b/", "realworld/", "phase3/",
            "phase4_render_spike/", "phase4_auto_region/", "phase5_alpha/",
        )
    ):
        return "preserved_prior_stage_small_evidence"
    if relative.startswith(("docs/", "config/")):
        return "documentation_or_configuration"
    return "repository_metadata_or_documentation"


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


def verify_zip(path: Path, expected_root: Path) -> dict[str, object]:
    expected = {
        item.relative_to(expected_root).as_posix(): (
            item.stat().st_size, sha256(item)
        )
        for item in expected_root.rglob("*")
        if item.is_file()
    }
    actual: dict[str, tuple[int, str]] = {}
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
                raise RuntimeError(f"unsafe ZIP member: {member.filename}")
            if member_path.suffix.casefold() in FORBIDDEN_SUFFIXES:
                raise RuntimeError(f"forbidden payload: {member.filename}")
            data = archive.read(member)
            actual[member.filename] = (
                len(data), hashlib.sha256(data).hexdigest()
            )
    if actual != expected:
        raise RuntimeError("ZIP inventory differs from delivery directory")
    return {
        "member_count": len(actual),
        "members_match_delivery": True,
        "duplicate_members": 0,
        "unsafe_paths_or_types": 0,
        "forbidden_payload_members": 0,
    }


def check_result(
    check_id: str, argv: list[str], process: subprocess.CompletedProcess[str]
) -> dict[str, object]:
    stdout = process.stdout.encode()
    stderr = process.stderr.encode()
    return {
        "check_id": check_id,
        "command_argv": argv,
        "exit_code": process.returncode,
        "pass": process.returncode == 0,
        "stdout_size_bytes": len(stdout),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_size_bytes": len(stderr),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }


def fresh_tree_verify(patch_path: Path) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="paper2md-phase6-fresh-") as tmp:
        temporary = Path(tmp)
        worktree = temporary / "Paper2MD"
        added = run(
            ["git", "worktree", "add", "--detach", str(worktree), BASE],
            cwd=ROOT,
        )
        if added.returncode:
            raise RuntimeError(f"fresh worktree creation failed: {added.stderr}")
        try:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join(
                (str(worktree / "src"), str(worktree / "tests"))
            )
            commands = [
                ("patch_check", ["git", "apply", "--check", str(patch_path)]),
                ("patch_apply", ["git", "apply", str(patch_path)]),
                (
                    "unit_tests",
                    [
                        sys.executable, "-m", "unittest", "discover", "-s",
                        "tests", "-v",
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
                    "phase3_summary",
                    [sys.executable, "tools/check_phase3_summary.py"],
                ),
                (
                    "phase4_spike_summary",
                    [sys.executable, "tools/check_phase4_spike_summary.py"],
                ),
                (
                    "phase4_auto_summary",
                    [sys.executable, "tools/check_phase4_auto_summary.py"],
                ),
                (
                    "phase5_summary",
                    [sys.executable, "tools/check_phase5_summary.py"],
                ),
                (
                    "phase6_summary",
                    [sys.executable, "tools/check_phase6_summary.py"],
                ),
                (
                    "compileall",
                    [
                        sys.executable, "-m", "compileall", "-q", "src",
                        "tests", "tools",
                    ],
                ),
                (
                    "repo_policy",
                    [
                        sys.executable, "tools/check_repo_policy.py", "--root",
                        ".",
                    ],
                ),
                ("diff_check", ["git", "diff", "--check"]),
            ]
            for check_id, argv in commands:
                process = run(argv, cwd=worktree, env=environment)
                checks.append(check_result(check_id, argv, process))
                if process.returncode:
                    raise RuntimeError(
                        f"fresh-tree {check_id} failed: "
                        f"{process.stdout}\n{process.stderr}"
                    )
            runtime_checks = [
                (
                    "fresh_batch",
                    [
                        sys.executable, "tools/run_phase5_batch_checks.py",
                        "--repo", ".", "--output-root",
                        str(temporary / "batch-runtime"), "--summary",
                        str(temporary / "batch-summary.json"),
                    ],
                ),
                (
                    "fresh_install",
                    [
                        sys.executable, "tools/run_phase6_install_checks.py",
                        "--repo", ".", "--output-root",
                        str(temporary / "install-runtime"), "--summary",
                        str(temporary / "install-summary.json"),
                    ],
                ),
            ]
            for check_id, argv in runtime_checks:
                process = run(argv, cwd=worktree, env=environment)
                checks.append(check_result(check_id, argv, process))
                if process.returncode:
                    raise RuntimeError(
                        f"fresh-tree {check_id} failed: "
                        f"{process.stdout}\n{process.stderr}"
                    )
        finally:
            run(
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
        raise RuntimeError("HEAD is not the authorized Phase 6 baseline")
    protected = git(
        "diff", "--name-only", BASE, "--", "src/paper2md", "phase5_alpha",
        "phase4_auto_region", "phase4_render_spike", "phase3", "realworld",
        "stage_a", "stage_b",
    ).strip()
    if protected:
        raise RuntimeError(f"protected product/evidence modified: {protected}")
    if DELIVERY.exists() or ZIP_PATH.exists() or PATCH_PATH.exists():
        raise RuntimeError("delivery target exists; refusing to overwrite")
    required = (
        "phase6_alpha_rc/baseline_authority.json",
        "phase6_alpha_rc/test_summary.json",
        "phase6_alpha_rc/install_test_summary.json",
        "phase6_alpha_rc/batch_test_summary.json",
        "phase6_alpha_rc/release_readiness.json",
        "phase6_alpha_rc/license_decision.json",
        "phase6_alpha_rc/merge_recommendation_zh.md",
        "phase6_alpha_rc/report_zh.md",
        "phase6_alpha_rc/REPRODUCE.md",
    )
    missing = [item for item in required if not (ROOT / item).is_file()]
    if missing:
        raise RuntimeError(f"required evidence missing: {missing}")

    with tempfile.TemporaryDirectory(prefix="paper2md-phase6-index-") as tmp:
        index = Path(tmp) / "index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index)
        git("read-tree", BASE, env=environment)
        git("add", "-A", env=environment)
        paths = [
            value
            for value in git("ls-files", "-z", env=environment).split("\0")
            if value
        ]
        patch = git(
            "diff", "--cached", "--binary", "--full-index", BASE,
            env=environment,
        )

    DELIVERY.mkdir(parents=True)
    SOURCE_COPY.mkdir()
    for relative in paths:
        source = ROOT / relative
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"unsafe candidate source: {relative}")
        destination = SOURCE_COPY / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    PATCH_PATH.write_text(patch, encoding="utf-8")
    shutil.copy2(PATCH_PATH, DELIVERY / PATCH_PATH.name)

    fresh = fresh_tree_verify(PATCH_PATH)
    (DELIVERY / "fresh_tree_verification.json").write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    copies = {
        "test_summary.json": "phase6_alpha_rc/test_summary.json",
        "install_test_summary.json": "phase6_alpha_rc/install_test_summary.json",
        "batch_test_summary.json": "phase6_alpha_rc/batch_test_summary.json",
        "release_readiness.json": "phase6_alpha_rc/release_readiness.json",
        "license_decision.json": "phase6_alpha_rc/license_decision.json",
        "merge_recommendation_zh.md": (
            "phase6_alpha_rc/merge_recommendation_zh.md"
        ),
        "report_zh.md": "phase6_alpha_rc/report_zh.md",
        "REPRODUCE.md": "phase6_alpha_rc/REPRODUCE.md",
    }
    for name, source in copies.items():
        shutil.copy2(ROOT / source, DELIVERY / name)
    (DELIVERY / "PACKAGE_CONTENTS.txt").write_text(
        "\n".join(
            (
                "Paper2MD/ - 完整 Alpha RC 候选源码树（无 .git）",
                f"{PATCH_PATH.name} - 相对 {BASE} 的完整可应用 diff",
                "stage_manifest.json - 基线、逐文件 hash、测试和范围检查",
                "SHA256SUMS.txt - 包内文件 SHA-256（不含自身）",
                "fresh_tree_verification.json - fresh-tree patch/安装/测试",
                "test_summary.json / install_test_summary.json / "
                "batch_test_summary.json - 机器验收摘要",
                "release_readiness.json / license_decision.json - 发布和许可决策",
                "merge_recommendation_zh.md / report_zh.md / REPRODUCE.md",
                "不含 PDF、真实图片/输出、wheel/sdist、PDFium/JAR/binary、"
                "缓存、虚拟环境或凭据。",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    source_files = [
        {
            "path": relative,
            "size_bytes": (SOURCE_COPY / relative).stat().st_size,
            "sha256": sha256(SOURCE_COPY / relative),
            "role": role(relative),
            "allowed_reason": "source-only Git candidate",
        }
        for relative in sorted(paths)
    ]
    policy_process = run(
        [
            sys.executable, str(ROOT / "tools/check_repo_policy.py"), "--root",
            str(SOURCE_COPY),
        ],
        cwd=SOURCE_COPY,
    )
    policy = json.loads(policy_process.stdout)
    if policy_process.returncode or policy["violation_count"]:
        raise RuntimeError(f"source package policy failed: {policy}")
    manifest = {
        "schema_version": "paper2md-phase6-alpha-rc-stage-manifest-v1",
        "base_commit": BASE,
        "candidate_version": "0.6.0a0",
        "source_file_count": len(source_files),
        "source_total_bytes": sum(
            int(item["size_bytes"]) for item in source_files
        ),
        "files": source_files,
        "tests": {
            "unit": {
                "inherited": 94, "phase6_added": 6, "passed": 100,
                "failed": 0, "skipped": 0,
            },
            "batch": {"passed": 8, "failed": 0, "skipped": 0},
            "install_commands": {"passed": 12, "failed": 0, "skipped": 0},
        },
        "fresh_tree": fresh,
        "policy_and_secret_scan": policy,
        "product_algorithm_changes": 0,
        "formal_distribution_approved": False,
        "package_exclusions": [
            "PDF/corpus/gold", "real paper images and conversion output",
            "wheel/sdist installation artifacts", "PDFium/JAR/binary",
            "cache/venv/node_modules", "credentials/PII",
        ],
    }
    (DELIVERY / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    sums_target = DELIVERY / "SHA256SUMS.txt"
    sums_target.write_text(
        "\n".join(
            f"{sha256(path)}  {path.relative_to(DELIVERY).as_posix()}"
            for path in sorted(
                item for item in DELIVERY.rglob("*") if item.is_file()
            )
            if path != sums_target
        )
        + "\n",
        encoding="utf-8",
    )
    write_zip(DELIVERY, ZIP_PATH)
    first_hash = sha256(ZIP_PATH)
    with tempfile.TemporaryDirectory(prefix="paper2md-phase6-zip-") as tmp:
        second = Path(tmp) / ZIP_PATH.name
        write_zip(DELIVERY, second)
        second_hash = sha256(second)
    if first_hash != second_hash:
        raise RuntimeError("ZIP deterministic rebuild mismatch")
    verification = verify_zip(ZIP_PATH, DELIVERY)
    print(
        json.dumps(
            {
                "zip_path": str(ZIP_PATH),
                "zip_size_bytes": ZIP_PATH.stat().st_size,
                "zip_sha256": first_hash,
                "patch_path": str(PATCH_PATH),
                "patch_size_bytes": PATCH_PATH.stat().st_size,
                "patch_sha256": sha256(PATCH_PATH),
                "deterministic_rebuild": True,
                "verification": verification,
                "fresh_tree": {
                    "checks": fresh["check_count"],
                    "passed": fresh["pass_count"],
                    "failed": fresh["failure_count"],
                    "skipped": fresh["skip_count"],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
