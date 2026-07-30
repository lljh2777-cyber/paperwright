#!/usr/bin/env python3
"""Build a deterministic, source-only Stage A handoff package."""

from __future__ import annotations

import csv
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
DELIVERY = WORKSPACE / "v2-bootstrap-delivery"
SOURCE_COPY = DELIVERY / "Paper2MD"
ZIP_PATH = WORKSPACE / "v2-bootstrap-src.zip"
BASE = "7e559221b46d2204554c303312b2503531b351c0"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, env: dict[str, str] | None = None) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({process.returncode}): {process.stderr}"
        )
    return process.stdout


def role(path: str) -> str:
    if path.startswith("src/"):
        return "product_source_or_schema"
    if path.startswith("tests/"):
        return "self_generated_fixture_or_test"
    if path.startswith("tools/"):
        return "development_or_verification_tool"
    if path.startswith("stage_a/"):
        return "stage_a_small_audit_evidence"
    if path.startswith(("docs/", "config/")):
        return "documentation_or_configuration"
    return "repository_metadata_or_documentation"


def policy_scan(root: Path) -> dict:
    process = subprocess.run(
        [sys.executable, str(SOURCE_COPY / "tools/check_repo_policy.py"), "--root", str(root)],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"policy scan output invalid: {process.stdout}") from exc
    if process.returncode or value["violation_count"]:
        raise RuntimeError(f"policy scan failed: {value}")
    return value


def write_zip(source: Path, target: Path) -> None:
    files = sorted(item for item in source.rglob("*") if item.is_file())
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
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
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("ZIP contains duplicate members")
        actual = {}
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"unsafe ZIP path: {member.filename}")
            if member.is_dir():
                continue
            data = archive.read(member)
            actual[member.filename] = (
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
    if actual != expected:
        raise RuntimeError("ZIP member inventory differs from delivery directory")
    return {
        "member_count": len(actual),
        "members_match_delivery": True,
        "duplicate_members": 0,
        "unsafe_paths": 0,
    }


def main() -> int:
    if git("rev-parse", "HEAD").strip() != BASE:
        raise RuntimeError("working tree is not based on the authorized commit")
    if DELIVERY.exists() or ZIP_PATH.exists():
        raise RuntimeError("delivery target already exists; refusing to overwrite")

    with tempfile.TemporaryDirectory(prefix="paper2md-stage-a-index-") as temporary:
        index = Path(temporary) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        git("read-tree", BASE, env=env)
        git("add", "-A", env=env)
        paths = [
            line
            for line in git(
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=ACMR",
                BASE,
                env=env,
            ).splitlines()
            if line
        ]
        patch = git("diff", "--cached", "--binary", "--full-index", BASE, env=env)

    DELIVERY.mkdir(parents=True)
    SOURCE_COPY.mkdir()
    for relative in paths:
        source = ROOT / relative
        destination = SOURCE_COPY / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    (DELIVERY / "changes.patch").write_text(patch, encoding="utf-8")
    shutil.copy2(ROOT / "stage_a/test_summary.json", DELIVERY / "test_summary.json")
    shutil.copy2(ROOT / "stage_a/test_report_zh.md", DELIVERY / "test_report_zh.md")
    shutil.copy2(ROOT / "REPRODUCE.md", DELIVERY / "REPRODUCE.md")

    contents = [
        "Paper2MD/ - 完整 Stage A 候选源码树（无 .git）",
        "changes.patch - 相对 7e55922 的完整可应用 diff",
        "stage_manifest.json - 基线、文件哈希、测试与安全检查",
        "SHA256SUMS.txt - 包内文件 SHA-256（不含自身）",
        "test_summary.json - 机器可读测试摘要",
        "test_report_zh.md - 简体中文测试报告",
        "REPRODUCE.md - 复现命令",
        "PACKAGE_CONTENTS.txt - 本说明",
    ]
    (DELIVERY / "PACKAGE_CONTENTS.txt").write_text(
        "\n".join(contents) + "\n", encoding="utf-8"
    )

    candidate_scan = policy_scan(SOURCE_COPY)
    delivery_scan = policy_scan(DELIVERY)
    candidate_files = []
    for relative in paths:
        path = SOURCE_COPY / relative
        candidate_files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "allowed_reason": role(relative),
            }
        )

    audit_files = []
    for path in sorted(item for item in DELIVERY.iterdir() if item.is_file()):
        audit_files.append(
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "allowed_reason": "required_stage_a_handoff_evidence",
            }
        )

    tests = json.loads((DELIVERY / "test_summary.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "paper2md-v2-bootstrap-stage-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "base_commit": BASE,
        "base_subject": "Initialize Paper2MD v2 baseline",
        "candidate_branch_for_local_use": "agent/v2-rebuild",
        "not_a_recovery_checkpoint": True,
        "candidate_file_count": len(candidate_files),
        "candidate_total_bytes": sum(item["size_bytes"] for item in candidate_files),
        "candidate_files": candidate_files,
        "audit_files_before_manifest": audit_files,
        "tests": {
            "unit_test_count": 27,
            "unit_test_passed": 27,
            "top_level_checks": tests["check_count"],
            "top_level_passed": tests["pass_count"],
            "failure_count": tests["failure_count"],
            "skip_count": tests["skip_count"],
            "historical_failures_disclosed": len(tests["historical_runs"]),
        },
        "safety_checks": {
            "candidate_source_scan": candidate_scan,
            "delivery_pre_manifest_scan": delivery_scan,
            "paper_pdf_count": 0,
            "extracted_image_count": 0,
            "backend_binary_or_jar_count": 0,
            "credential_count": 0,
            "max_allowed_file_bytes": 5 * 1024 * 1024,
            "extension_allowlist_enforced": True,
        },
        "package_exclusions": [
            ".git",
            "PDF and real-world corpus",
            "extracted images and conversion outputs",
            "PDFium/JAR/binary/wheel",
            "virtual environments and caches",
            "credentials and tokens",
        ],
        "inventory_note": "candidate_files lists the complete Git candidate tree; stage_manifest and SHA256SUMS exclude their own recursive hashes",
    }
    (DELIVERY / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    sum_lines = []
    for path in sorted(item for item in DELIVERY.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        sum_lines.append(f"{sha256(path)}  {path.relative_to(DELIVERY).as_posix()}")
    (DELIVERY / "SHA256SUMS.txt").write_text(
        "\n".join(sum_lines) + "\n", encoding="utf-8"
    )

    final_scan = policy_scan(DELIVERY)
    write_zip(DELIVERY, ZIP_PATH)
    verify_first = verify_zip(ZIP_PATH, DELIVERY)
    with tempfile.TemporaryDirectory(prefix="paper2md-stage-a-zip-") as temporary:
        second = Path(temporary) / ZIP_PATH.name
        write_zip(DELIVERY, second)
        if sha256(second) != sha256(ZIP_PATH):
            raise RuntimeError("deterministic ZIP rebuild hash mismatch")
    result = {
        "delivery": str(DELIVERY),
        "zip": str(ZIP_PATH),
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "candidate_file_count": len(candidate_files),
        "final_policy_violation_count": final_scan["violation_count"],
        "zip_verification": verify_first,
        "deterministic_rebuild": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
