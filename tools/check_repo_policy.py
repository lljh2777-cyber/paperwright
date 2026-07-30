#!/usr/bin/env python3
"""Reject files outside the Stage A source-only repository policy."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_SUFFIXES = {
    "",
    ".md",
    ".py",
    ".json",
    ".toml",
    ".txt",
    ".patch",
    ".csv",
    ".yml",
    ".yaml",
}
DENIED_SUFFIXES = {
    ".pdf",
    ".jar",
    ".class",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".whl",
    ".deb",
    ".rpm",
    ".7z",
    ".rar",
    ".tgz",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}
DENIED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "corpus",
    "outputs",
    "output",
    "cache",
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\\bgh[pousr]_[A-Za-z0-9_]{20,}\\b"),
    "aws_access_key": re.compile(r"\\bAKIA[0-9A-Z]{16}\\b"),
    "generic_secret_assignment": re.compile(
        r"(?i)\\b(?:password|secret|api[_-]?key|access[_-]?token)\\s*[:=]\\s*['\\\"][^'\\\"]{8,}['\\\"]"
    ),
}


def scan(root: Path) -> dict:
    violations: list[dict[str, str | int]] = []
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        if any(part in DENIED_PARTS for part in path.relative_to(root).parts):
            continue
        size = path.stat().st_size
        files.append(rel)
        if path.is_symlink():
            violations.append({"path": rel, "reason": "symlink_not_allowed", "size": size})
        if size > MAX_BYTES:
            violations.append({"path": rel, "reason": "file_too_large", "size": size})
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            violations.append(
                {"path": rel, "reason": "extension_not_allowlisted", "size": size}
            )
        if path.suffix.lower() in DENIED_SUFFIXES:
            violations.append({"path": rel, "reason": "denied_extension", "size": size})
        if size <= 1024 * 1024:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    violations.append(
                        {"path": rel, "reason": f"secret_pattern:{name}", "size": size}
                    )
    return {
        "policy_version": "stage-a-source-only-v1",
        "extension_allowlist": sorted(ALLOWED_SUFFIXES),
        "max_file_bytes": MAX_BYTES,
        "scanned_file_count": len(files),
        "violation_count": len(violations),
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = scan(args.root.resolve())
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if result["violation_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
