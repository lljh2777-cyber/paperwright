#!/usr/bin/env python3
"""Build temporary Alpha distributions and test installed console entrypoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

from pdf_fixture_factory import create_auto_region_fixture, create_born_digital_fixture

FORBIDDEN_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jar",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".whl",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    process = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    stdout = process.stdout.encode()
    stderr = process.stderr.encode()
    return {
        "command_argv": argv,
        "exit_code": process.returncode,
        "stdout_size_bytes": len(stdout),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_size_bytes": len(stderr),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_text": process.stdout,
        "stderr_text": process.stderr,
    }


def source_paths(repo: Path) -> list[str]:
    process = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return sorted(
        item.decode("utf-8")
        for item in process.stdout.split(b"\0")
        if item
    )


def copy_candidate(repo: Path, destination: Path) -> None:
    destination.mkdir()
    for relative in source_paths(repo):
        source = repo / relative
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"unsafe candidate source: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def audit_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("wheel contains duplicate members")
        forbidden = [
            name
            for name in names
            if Path(name).suffix.casefold() in FORBIDDEN_SUFFIXES
        ]
        if forbidden:
            raise RuntimeError(f"wheel contains forbidden payload: {forbidden}")
        required = {
            "paperwright/schemas/article_model.schema.json",
            "paperwright/schemas/batch_summary.schema.json",
            "paperwright/schemas/completeness.schema.json",
            "paperwright/schemas/caption_relation_dataset.schema.json",
            "paperwright/schemas/cross_page_caption_review.schema.json",
            "paperwright/schemas/cross_page_caption_task.schema.json",
            "paperwright/schemas/final_layout.schema.json",
            "paperwright/schemas/hybrid_run.schema.json",
            "paperwright/schemas/issue_routing.schema.json",
            "paperwright/schemas/source_evidence.schema.json",
            "paperwright/schemas/layout_task.schema.json",
            "paperwright/schemas/manifest.schema.json",
            "paperwright/schemas/physical_document.schema.json",
            "paperwright/schemas/reader.schema.json",
            "paperwright/schemas/text_review.schema.json",
            "paperwright/schemas/text_task.schema.json",
            "paperwright/schemas/visual_relation_review.schema.json",
            "paperwright/schemas/synthesis_run.schema.json",
        }
        if not required.issubset(names):
            raise RuntimeError("wheel is missing package schemas")
        required_tools = {
            "share/paperwright/tools/run_cross_page_caption_review.py",
            "share/paperwright/tools/run_routing_plan.py",
            "share/paperwright/tools/run_text_review.py",
            "share/paperwright/tools/run_text_synthesize.py",
            "share/paperwright/tools/run_visual_review.py",
            "share/paperwright/tools/validate_relation_dataset.py",
        }
        if any(
            not any(name.endswith(relative) for name in names)
            for relative in required_tools
        ):
            raise RuntimeError("wheel is missing Hybrid resolver tools")
        total_uncompressed = sum(item.file_size for item in archive.infolist())
    return {
        "member_count": len(names),
        "total_uncompressed_bytes": total_uncompressed,
        "forbidden_members": 0,
        "required_schemas_present": True,
    }


def audit_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [item.name for item in members if item.isfile()]
        forbidden = [
            name
            for name in names
            if Path(name).suffix.casefold() in FORBIDDEN_SUFFIXES
        ]
        if forbidden:
            raise RuntimeError(f"sdist contains forbidden payload: {forbidden}")
        if any(
            Path(item.name).is_absolute()
            or ".." in Path(item.name).parts
            or item.issym()
            or item.islnk()
            for item in members
        ):
            raise RuntimeError("sdist contains unsafe path or link")
    return {
        "regular_file_count": len(names),
        "forbidden_members": 0,
        "unsafe_members": 0,
    }


def output_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix() != "batch_summary.json"
    }


def exercise_install(
    *,
    artifact: Path,
    kind: str,
    runtime_root: Path,
    fixtures: Path,
    model_fixture: Path,
) -> dict[str, object]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PIP_CACHE_DIR": str(runtime_root / f"pip-cache-{kind}"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    venv_root = runtime_root / f"venv-{kind}"
    venv.EnvBuilder(
        with_pip=True,
        clear=False,
        system_site_packages=True,
    ).create(venv_root)
    scripts = venv_root / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    console = scripts / ("paperwright.exe" if os.name == "nt" else "paperwright")
    install_argv = [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-deps",
    ]
    install_argv.append(str(artifact))
    checks = []
    install = run(install_argv, cwd=runtime_root, env=environment)
    checks.append({"check_id": "install", **install})
    if install["exit_code"]:
        raise RuntimeError(f"{kind} install failed: {install['stderr_text']}")

    commands = [
        ("version", [str(console), "--version"]),
        ("help", [str(console), "--help"]),
        (
            "convert",
            [
                str(console),
                "convert",
                str(fixtures / "a.pdf"),
                str(runtime_root / f"{kind}-single"),
            ],
        ),
        (
            "batch",
            [
                str(console),
                "batch",
                str(runtime_root / f"{kind}-batch"),
                "--input-dir",
                str(fixtures),
            ],
        ),
        (
            "validate_model",
            [str(console), "validate-model", str(model_fixture)],
        ),
    ]
    for check_id, argv in commands:
        result = run(argv, cwd=runtime_root, env=environment)
        checks.append({"check_id": check_id, **result})
        if result["exit_code"]:
            raise RuntimeError(
                f"{kind} {check_id} failed: {result['stderr_text']}"
            )
    batch_summary = json.loads(
        (runtime_root / f"{kind}-batch/batch_summary.json").read_text(
            encoding="utf-8"
        )
    )
    portable_checks = []
    for item in checks:
        portable = {
            key: value
            for key, value in item.items()
            if key not in {"stdout_text", "stderr_text"}
        }
        portable["command_argv"] = [
            str(value).replace(str(runtime_root), "<runtime>")
            for value in portable["command_argv"]
        ]
        portable_checks.append(portable)
    return {
        "kind": kind,
        "venv_system_site_packages": True,
        "dependency_access": (
            "isolated venv and installed distribution; sdist build requirements "
            "use PEP 517 isolation, while locked PDFium/Pillow are read from "
            "the pre-verified runtime"
        ),
        "checks": portable_checks,
        "pass_count": len(checks),
        "failure_count": 0,
        "skip_count": 0,
        "batch_deterministic_content_sha256": batch_summary[
            "deterministic_content_sha256"
        ],
        "batch_output_tree": output_tree(
            runtime_root / f"{kind}-batch"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.output_root.exists():
        raise RuntimeError("install-check output root exists")
    args.output_root.mkdir(parents=True)

    source_root = args.output_root / "source"
    copy_candidate(repo, source_root)
    dist = args.output_root / "dist"
    dist.mkdir()
    current = Path.cwd()
    try:
        os.chdir(source_root)
        from setuptools.build_meta import build_sdist, build_wheel

        wheel_name = build_wheel(str(dist))
        sdist_name = build_sdist(str(dist))
    finally:
        os.chdir(current)
    wheel = dist / wheel_name
    sdist = dist / sdist_name
    if not wheel.is_file() or not sdist.is_file():
        raise RuntimeError("build did not produce wheel and sdist")

    fixtures = args.output_root / "fixtures"
    fixtures.mkdir()
    create_born_digital_fixture(fixtures / "a.pdf")
    create_auto_region_fixture(fixtures / "b.pdf", "mixed")
    model_fixture = source_root / "tests/fixtures/physical_document.minimal.json"
    installs = [
        exercise_install(
            artifact=wheel,
            kind="wheel",
            runtime_root=args.output_root,
            fixtures=fixtures,
            model_fixture=model_fixture,
        ),
        exercise_install(
            artifact=sdist,
            kind="sdist",
            runtime_root=args.output_root,
            fixtures=fixtures,
            model_fixture=model_fixture,
        ),
    ]
    deterministic_equal = (
        installs[0]["batch_deterministic_content_sha256"]
        == installs[1]["batch_deterministic_content_sha256"]
        and installs[0]["batch_output_tree"] == installs[1]["batch_output_tree"]
    )
    if not deterministic_equal:
        raise RuntimeError("wheel/sdist installed batch outputs differ")
    summary = {
        "schema_version": "paperwright-phase5-install-test-summary-v1",
        "platform_scope": "Linux Work cloud only; Windows remains for local review",
        "python": sys.version,
        "artifacts": [
            {
                "kind": "wheel",
                "filename": wheel.name,
                "size_bytes": wheel.stat().st_size,
                "sha256": sha256(wheel),
                "contents": audit_wheel(wheel),
            },
            {
                "kind": "sdist",
                "filename": sdist.name,
                "size_bytes": sdist.stat().st_size,
                "sha256": sha256(sdist),
                "contents": audit_sdist(sdist),
            },
        ],
        "installs": installs,
        "install_count": 2,
        "command_check_count": sum(
            len(item["checks"]) for item in installs
        ),
        "pass_count": sum(item["pass_count"] for item in installs),
        "failure_count": 0,
        "skip_count": 0,
        "wheel_sdist_outputs_deterministic": deterministic_equal,
        "artifacts_are_runtime_only_not_for_source_package": True,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
