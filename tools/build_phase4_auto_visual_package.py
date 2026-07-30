#!/usr/bin/env python3
"""Package only the minimum Phase 4 auto visual evidence as a deterministic ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_zip(source: Path, target: Path) -> None:
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(item for item in source.iterdir() if item.is_file()):
            info = zipfile.ZipInfo(path.name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-dir", type=Path, required=True)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("visual package already exists")
    source_manifest = json.loads(
        (args.visual_dir / "visual_evidence_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    with tempfile.TemporaryDirectory(
        prefix="paper2md-phase4-auto-visual-"
    ) as temporary:
        staging = Path(temporary)
        files = []
        for path in sorted(args.visual_dir.glob("*.png")):
            with Image.open(path) as image:
                image.verify()
            target = staging / path.name
            target.write_bytes(path.read_bytes())
            files.append(
                {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        source_manifest["source_package"] = {
            "path": args.source_zip.name,
            "size_bytes": args.source_zip.stat().st_size,
            "sha256": sha256(args.source_zip),
        }
        source_manifest["files"] = files
        manifest_path = staging / "visual_evidence_manifest.json"
        manifest_path.write_text(
            json.dumps(
                source_manifest, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        (staging / "视觉核验说明.md").write_text(
            "# Phase 4 auto region-render 视觉核验\n\n"
            "- RW2-005 p3：Figure 1 的位图与矢量面板完整，caption 未烧入。\n"
            "- RW2-007 p5：Fig. 2 的组织图、热图、散点和条形图完整。\n"
            "- RW2-005 p7：可见 continued on next page，安全拒绝生成 region。\n"
            "- contact sheet 依次展示带 bbox 原页、Phase 3 native 对照和最终区域图。\n"
            "- 本包不含 PDF、源码、依赖、凭据或转换目录。\n",
            encoding="utf-8",
        )
        sums = staging / "SHA256SUMS.txt"
        sums.write_text(
            "\n".join(
                f"{sha256(path)}  {path.name}"
                for path in sorted(staging.iterdir())
                if path.is_file() and path != sums
            )
            + "\n",
            encoding="utf-8",
        )
        write_zip(staging, args.output)
        first_hash = sha256(args.output)
        with tempfile.TemporaryDirectory(
            prefix="paper2md-phase4-auto-visual-rebuild-"
        ) as rebuild:
            second = Path(rebuild) / args.output.name
            write_zip(staging, second)
            second_hash = sha256(second)
        if first_hash != second_hash:
            raise RuntimeError("visual ZIP deterministic rebuild mismatch")
    with zipfile.ZipFile(args.output) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("visual ZIP contains duplicate names")
        for member in archive.infolist():
            path = Path(member.filename)
            unix_type = (member.external_attr >> 16) & 0o170000
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.is_dir()
                or unix_type not in {0, 0o100000}
            ):
                raise RuntimeError(f"unsafe member {member.filename}")
            if path.suffix.casefold() not in {".png", ".json", ".txt", ".md"}:
                raise RuntimeError(f"unexpected member {member.filename}")
    print(
        json.dumps(
            {
                "path": str(args.output),
                "size_bytes": args.output.stat().st_size,
                "sha256": sha256(args.output),
                "member_count": len(names),
                "deterministic_rebuild": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
