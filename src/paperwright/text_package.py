"""Build an atomic, self-contained package from a validated text review."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Mapping

from .article_model import (
    article_model_to_reader,
    canonical_article_model_json,
    render_article_markdown,
    validate_article_model,
)
from .article_tree import (
    article_tree_to_article_model,
    build_final_article_tree,
    canonical_final_article_tree_json,
    validate_final_article_tree,
)
from .exceptions import (
    ConfigurationError,
    ContractValidationError,
    OutputConflictError,
)
from .manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    TEXT_REVIEWED_MANIFEST_VERSION,
    TEXT_SYNTHESIZED_MANIFEST_VERSION,
    canonical_manifest_json,
    sha256_file,
    validate_manifest,
)
from .reader import canonical_reader_json
from .synthesize import (
    SYNTHESIS_RUN_CONTRACT_VERSION,
    canonical_synthesis_run_json,
    validate_synthesis_run,
)
from .text_review import (
    apply_text_review,
    canonical_text_review_json,
    canonical_text_task_json,
    text_task_sha256,
    validate_text_review,
    validate_text_task,
)


TEXT_PACKAGE_VALIDATION_VERSION = "paperwright-text-package-validation-v0.1"
_ARTICLE_PATH = "article.md"
_MODEL_PATH = "_paperwright/article-model.json"
_ARTICLE_TREE_PATH = "_paperwright/article-tree.json"
_READER_PATH = "_paperwright/reader.json"
_MANIFEST_PATH = "_paperwright/manifest.json"
_TASK_PATH = "_paperwright/06-text-review/text-task.json"
_REVIEW_PATH = "_paperwright/06-text-review/text-review.json"
_VALIDATION_PATH = "_paperwright/06-text-review/validation-report.json"
_VALIDATION_MARKDOWN_PATH = "_paperwright/06-text-review/validation-report.md"
_SYNTHESIS_RUN_PATH = "_paperwright/06-text-review/synthesize-run.json"
_SOURCE_ARTICLE_MODEL_PATH = "_paperwright/06-text-review/source-article-model.json"


@dataclass(frozen=True)
class TextPackageResult:
    output_dir: Path
    manifest: dict[str, Any]
    operation_count: int


def _safe_package_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ContractValidationError("manifest output path 非法")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ContractValidationError("manifest output path 越界")
    candidate = root.joinpath(*pure.parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractValidationError("manifest output path 越界") from exc
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ContractValidationError("文本派生包不接受符号链接")
    return candidate


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} 必须是 JSON object")
    return value


def _verify_inventory(root: Path, manifest: Mapping[str, Any]) -> None:
    for output in manifest["outputs"]:
        path = _safe_package_path(root, output["path"])
        if (
            not path.is_file()
            or path.stat().st_size != output["size_bytes"]
            or sha256_file(path) != output["sha256"]
        ):
            raise ContractValidationError(
                f"源文档包文件缺失或哈希不匹配: {output['path']}"
            )


def _load_source_package(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    manifest_path = root / PurePosixPath(_MANIFEST_PATH)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ConfigurationError("源文档包缺少 _paperwright/manifest.json")
    manifest = _load_json_object(manifest_path, "manifest")
    validate_manifest(manifest)
    if manifest["manifest_version"] != HYBRID_LAYOUT_MANIFEST_VERSION:
        raise ContractValidationError(
            "text-package v0.1 只接受 manifest v0.9 源文档包"
        )
    if manifest_path.read_text(encoding="utf-8") != canonical_manifest_json(
        manifest
    ):
        raise ContractValidationError("源 manifest 不是规范 JSON")
    _verify_inventory(root, manifest)

    model_path = _safe_package_path(root, _MODEL_PATH)
    model = _load_json_object(model_path, "article model")
    canonical_model = canonical_article_model_json(model)
    if model_path.read_text(encoding="utf-8") != canonical_model:
        raise ContractValidationError("源 article model 不是规范 JSON")
    validate_article_model(model, root=root)
    if manifest["article_model"]["sha256"] != sha256_file(model_path):
        raise ContractValidationError("manifest article model hash 不匹配")

    article_tree: dict[str, Any] | None = None
    tree_path = _safe_package_path(root, _ARTICLE_TREE_PATH)
    if tree_path.is_file():
        article_tree = _load_json_object(tree_path, "final ArticleTree")
        if tree_path.read_text(
            encoding="utf-8"
        ) != canonical_final_article_tree_json(article_tree):
            raise ContractValidationError("源 final ArticleTree 不是规范 JSON")
        validate_final_article_tree(article_tree, root=root)
        if article_tree_to_article_model(article_tree) != model:
            raise ContractValidationError(
                "源 article model 不是 final ArticleTree 的确定性投影"
            )

    article_path = _safe_package_path(root, _ARTICLE_PATH)
    if article_path.read_text(encoding="utf-8") != render_article_markdown(model):
        raise ContractValidationError("源 article.md 与 article model 不一致")
    reader_path = _safe_package_path(root, _READER_PATH)
    actual_reader = _load_json_object(reader_path, "reader")
    expected_reader = article_model_to_reader(model, root=root)
    if canonical_reader_json(actual_reader) != canonical_reader_json(
        expected_reader
    ):
        raise ContractValidationError("源 reader 与 article model 不一致")
    return manifest, model, article_tree


def validate_text_reviewed_package(root: Path) -> dict[str, Any]:
    """Validate a persisted manifest v0.10/v0.11 package and its hash chain."""

    package_root = root.expanduser().resolve()
    if not package_root.is_dir():
        raise ConfigurationError("文本派生包目录不存在")
    manifest_path = _safe_package_path(package_root, _MANIFEST_PATH)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ConfigurationError("文本派生包缺少 manifest.json")
    manifest = _load_json_object(manifest_path, "manifest")
    validate_manifest(manifest)
    if manifest["manifest_version"] not in {
        TEXT_REVIEWED_MANIFEST_VERSION,
        TEXT_SYNTHESIZED_MANIFEST_VERSION,
    }:
        raise ContractValidationError("文本派生包必须使用 manifest v0.10/v0.11")
    if manifest_path.read_text(encoding="utf-8") != canonical_manifest_json(
        manifest
    ):
        raise ContractValidationError("文本派生 manifest 不是规范 JSON")
    _verify_inventory(package_root, manifest)

    model_path = _safe_package_path(package_root, _MODEL_PATH)
    model = _load_json_object(model_path, "article model")
    if model_path.read_text(encoding="utf-8") != canonical_article_model_json(
        model
    ):
        raise ContractValidationError("文本派生 article model 不是规范 JSON")
    validate_article_model(model, root=package_root)
    if (
        model["source_sha256"] != manifest["source_sha256"]
        or sha256_file(model_path) != manifest["article_model"]["sha256"]
    ):
        raise ContractValidationError("文本派生 article model 绑定不一致")

    tree_path = _safe_package_path(package_root, _ARTICLE_TREE_PATH)
    if tree_path.is_file():
        article_tree = _load_json_object(tree_path, "final ArticleTree")
        if tree_path.read_text(
            encoding="utf-8"
        ) != canonical_final_article_tree_json(article_tree):
            raise ContractValidationError("文本派生 final ArticleTree 不是规范 JSON")
        validate_final_article_tree(article_tree, root=package_root)
        if article_tree_to_article_model(article_tree) != model:
            raise ContractValidationError(
                "文本派生 article model 不是 final ArticleTree 的投影"
            )

    article_path = _safe_package_path(package_root, _ARTICLE_PATH)
    if article_path.read_text(encoding="utf-8") != render_article_markdown(model):
        raise ContractValidationError("文本派生 article 投影不一致")
    reader_path = _safe_package_path(package_root, _READER_PATH)
    actual_reader = _load_json_object(reader_path, "reader")
    expected_reader = article_model_to_reader(model, root=package_root)
    if canonical_reader_json(actual_reader) != canonical_reader_json(
        expected_reader
    ):
        raise ContractValidationError("文本派生 reader 投影不一致")

    task_path = _safe_package_path(package_root, _TASK_PATH)
    review_path = _safe_package_path(package_root, _REVIEW_PATH)
    task = _load_json_object(task_path, "text task")
    review = _load_json_object(review_path, "text review")
    validate_text_task(task)
    validate_text_review(review, task=task)
    if task_path.read_text(encoding="utf-8") != canonical_text_task_json(task):
        raise ContractValidationError("文本派生 task 不是规范 JSON")
    if review_path.read_text(encoding="utf-8") != canonical_text_review_json(
        review,
        task=task,
    ):
        raise ContractValidationError("文本派生 review 不是规范 JSON")

    summary = manifest["text_review"]
    if (
        task["source_sha256"] != manifest["source_sha256"]
        or review["source_sha256"] != manifest["source_sha256"]
        or summary["source_article_model_sha256"]
        != task["article_model"]["sha256"]
        or summary["task_sha256"] != sha256_file(task_path)
        or summary["review_sha256"] != sha256_file(review_path)
        or summary["reviewer"] != review["reviewer"]
        or summary["operation_count"] != len(review["operations"])
    ):
        raise ContractValidationError("文本派生 task/review 溯源不一致")

    synthesis_run_path: Path | None = None
    if manifest["manifest_version"] == TEXT_SYNTHESIZED_MANIFEST_VERSION:
        synthesis = manifest["synthesis_run"]
        source_model_path = _safe_package_path(
            package_root, synthesis["source_article_model_path"]
        )
        source_model = _load_json_object(
            source_model_path, "source article model"
        )
        if source_model_path.read_text(
            encoding="utf-8"
        ) != canonical_article_model_json(source_model):
            raise ContractValidationError("源 article model 副本不是规范 JSON")
        validate_article_model(source_model, root=package_root)
        if (
            sha256_file(source_model_path)
            != synthesis["source_article_model_sha256"]
            or synthesis["source_article_model_sha256"]
            != task["article_model"]["sha256"]
        ):
            raise ContractValidationError("源 article model 副本哈希不匹配")

        synthesis_run_path = _safe_package_path(
            package_root, synthesis["path"]
        )
        run = _load_json_object(synthesis_run_path, "synthesis run")
        if synthesis_run_path.read_text(
            encoding="utf-8"
        ) != canonical_synthesis_run_json(run):
            raise ContractValidationError("synthesis run 不是规范 JSON")
        if sha256_file(synthesis_run_path) != synthesis["sha256"]:
            raise ContractValidationError("synthesis run 哈希不匹配")
        validate_synthesis_run(
            run,
            task=task,
            article_model=source_model,
            review=review,
        )

    validation_path = _safe_package_path(package_root, _VALIDATION_PATH)
    validation = _load_json_object(validation_path, "text validation report")
    required_checks = {
        "source_inventory",
        "source_projections",
        "task_model_binding",
        "review_task_binding",
        "output_assets",
        "output_projections",
    }
    expected_fields = {
        "contract_version",
        "status",
        "source_sha256",
        "parent_manifest_sha256",
        "source_article_model_sha256",
        "result_article_model_sha256",
        "task_sha256",
        "review_sha256",
        "reviewer",
        "operation_count",
        "checks",
    }
    if manifest["manifest_version"] == TEXT_SYNTHESIZED_MANIFEST_VERSION:
        required_checks.add("synthesis_run_replay")
        expected_fields.add("synthesis_run_sha256")
    if (
        set(validation) != expected_fields
        or validation["contract_version"] != TEXT_PACKAGE_VALIDATION_VERSION
        or validation["status"] != "valid"
        or not isinstance(validation["checks"], dict)
        or set(validation["checks"]) != required_checks
        or any(value is not True for value in validation["checks"].values())
        or validation["source_sha256"] != manifest["source_sha256"]
        or validation["parent_manifest_sha256"]
        != summary["parent_manifest_sha256"]
        or validation["source_article_model_sha256"]
        != summary["source_article_model_sha256"]
        or validation["result_article_model_sha256"] != sha256_file(model_path)
        or validation["task_sha256"] != summary["task_sha256"]
        or validation["review_sha256"] != summary["review_sha256"]
        or validation["reviewer"] != summary["reviewer"]
        or validation["operation_count"] != summary["operation_count"]
        or sha256_file(validation_path) != summary["validation_sha256"]
        or (
            manifest["manifest_version"] == TEXT_SYNTHESIZED_MANIFEST_VERSION
            and validation["synthesis_run_sha256"]
            != sha256_file(synthesis_run_path)
        )
    ):
        raise ContractValidationError("文本派生 validation report 不一致")
    return manifest


def _copy_manifest_outputs(
    source_root: Path,
    destination_root: Path,
    manifest: Mapping[str, Any],
) -> None:
    for output in sorted(manifest["outputs"], key=lambda item: item["path"]):
        source = _safe_package_path(source_root, output["path"])
        destination = _safe_package_path(destination_root, output["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _write_text(root: Path, relative: str, value: str) -> Path:
    path = _safe_package_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return path


def _output_record(root: Path, relative: str, role: str) -> dict[str, Any]:
    path = _safe_package_path(root, relative)
    return {
        "path": relative,
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _validation_markdown(value: Mapping[str, Any]) -> str:
    checks = value["checks"]
    lines = [
        "# PaperWright Text Review Validation",
        "",
        f"- Status: `{value['status']}`",
        f"- Reviewer: `{value['reviewer']}`",
        f"- Operations: `{value['operation_count']}`",
        f"- Task SHA-256: `{value['task_sha256']}`",
        f"- Review SHA-256: `{value['review_sha256']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- {name}: `{'PASS' if passed else 'FAIL'}`"
        for name, passed in sorted(checks.items())
    )
    return "\n".join(lines) + "\n"


def _package_paths(source_root: Path, destination: Path) -> tuple[Path, Path]:
    source = source_root.expanduser().resolve()
    target = destination.expanduser().resolve()
    if not source.is_dir():
        raise ConfigurationError("源文档包目录不存在")
    if target.exists():
        raise OutputConflictError(f"输出目录已存在，拒绝覆盖: {target}")
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise OutputConflictError("输出目录不能位于源文档包内")
    try:
        source.relative_to(target)
    except ValueError:
        pass
    else:
        raise OutputConflictError("源文档包不能位于输出目录内")
    target.parent.mkdir(parents=True, exist_ok=True)
    return source, target


def build_text_reviewed_package(
    source_root: Path,
    task: Mapping[str, Any],
    review: Mapping[str, Any],
    destination: Path,
    *,
    synthesis_run: Mapping[str, Any] | None = None,
) -> TextPackageResult:
    """Create a manifest v0.10 package, or v0.11 when a synthesis run is
    attached, without mutating the v0.9 parent."""

    source, target = _package_paths(source_root, destination)
    source_manifest, source_model, source_article_tree = _load_source_package(
        source
    )
    validate_text_task(task, article_model=source_model)
    validate_text_review(review, task=task)
    if synthesis_run is not None:
        validate_synthesis_run(
            synthesis_run,
            task=task,
            article_model=source_model,
            review=review,
        )
    reviewed_model_candidate = apply_text_review(
        source_model,
        task=task,
        review=review,
    )
    review_input_sha256 = hashlib.sha256(
        (
            text_task_sha256(task)
            + "\0"
            + canonical_text_review_json(review, task=task)
        ).encode("utf-8")
    ).hexdigest()
    reviewed_article_tree = build_final_article_tree(
        source_sha256=str(reviewed_model_candidate["source_sha256"]),
        physical_document_sha256=(
            source_article_tree["physical_document_sha256"]
            if source_article_tree is not None
            else None
        ),
        structure_input_kind="text_review",
        structure_input_sha256=review_input_sha256,
        blocks=reviewed_model_candidate["blocks"],
        markdown_by_id={
            str(item["id"]): str(item["markdown"])
            for item in reviewed_model_candidate["blocks"]
        },
        assets=reviewed_model_candidate["assets"],
        relations=reviewed_model_candidate["relations"],
    )
    reviewed_model = article_tree_to_article_model(reviewed_article_tree)

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.paperwright-text-",
            dir=target.parent,
        )
    )
    try:
        _copy_manifest_outputs(source, temporary, source_manifest)
        _write_text(
            temporary,
            _ARTICLE_TREE_PATH,
            canonical_final_article_tree_json(reviewed_article_tree),
        )
        article_path = _write_text(
            temporary,
            _ARTICLE_PATH,
            render_article_markdown(reviewed_model),
        )
        model_path = _write_text(
            temporary,
            _MODEL_PATH,
            canonical_article_model_json(reviewed_model),
        )
        reader_value = article_model_to_reader(reviewed_model, root=temporary)
        reader_path = _write_text(
            temporary,
            _READER_PATH,
            canonical_reader_json(reader_value),
        )
        task_path = _write_text(
            temporary,
            _TASK_PATH,
            canonical_text_task_json(task),
        )
        review_path = _write_text(
            temporary,
            _REVIEW_PATH,
            canonical_text_review_json(review, task=task),
        )
        source_model_path: Path | None = None
        synthesis_path: Path | None = None
        if synthesis_run is not None:
            source_model_path = _write_text(
                temporary,
                _SOURCE_ARTICLE_MODEL_PATH,
                canonical_article_model_json(source_model),
            )
            synthesis_path = _write_text(
                temporary,
                _SYNTHESIS_RUN_PATH,
                canonical_synthesis_run_json(synthesis_run),
            )

        validate_article_model(reviewed_model, root=temporary)
        validate_final_article_tree(reviewed_article_tree, root=temporary)
        if article_path.read_text(encoding="utf-8") != render_article_markdown(
            reviewed_model
        ):
            raise ContractValidationError("文本派生 article 投影失败")
        if canonical_reader_json(
            _load_json_object(reader_path, "reader")
        ) != canonical_reader_json(reader_value):
            raise ContractValidationError("文本派生 reader 投影失败")

        parent_manifest_path = source / PurePosixPath(_MANIFEST_PATH)
        validation = {
            "contract_version": TEXT_PACKAGE_VALIDATION_VERSION,
            "status": "valid",
            "source_sha256": reviewed_model["source_sha256"],
            "parent_manifest_sha256": sha256_file(parent_manifest_path),
            "source_article_model_sha256": task["article_model"]["sha256"],
            "result_article_model_sha256": sha256_file(model_path),
            "task_sha256": text_task_sha256(task),
            "review_sha256": sha256_file(review_path),
            "reviewer": review["reviewer"],
            "operation_count": len(review["operations"]),
            "checks": {
                "source_inventory": True,
                "source_projections": True,
                "task_model_binding": True,
                "review_task_binding": True,
                "output_assets": True,
                "output_projections": True,
            },
        }
        if synthesis_run is not None:
            validation["synthesis_run_sha256"] = sha256_file(synthesis_path)
            validation["checks"]["synthesis_run_replay"] = True
        validation_path = _write_text(
            temporary,
            _VALIDATION_PATH,
            json.dumps(
                validation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n",
        )
        validation_markdown_path = _write_text(
            temporary,
            _VALIDATION_MARKDOWN_PATH,
            _validation_markdown(validation),
        )

        replacements = {
            _ARTICLE_PATH: _output_record(temporary, _ARTICLE_PATH, "markdown"),
            _ARTICLE_TREE_PATH: _output_record(
                temporary,
                _ARTICLE_TREE_PATH,
                "article_tree",
            ),
            _MODEL_PATH: _output_record(
                temporary, _MODEL_PATH, "article_model"
            ),
            _READER_PATH: _output_record(
                temporary, _READER_PATH, "reader_index"
            ),
        }
        outputs = [
            deepcopy(output)
            for output in source_manifest["outputs"]
            if output["path"] not in replacements
        ]
        outputs.extend(replacements.values())
        outputs.extend(
            (
                _output_record(temporary, _TASK_PATH, "text_task"),
                _output_record(temporary, _REVIEW_PATH, "text_review"),
                _output_record(
                    temporary,
                    _VALIDATION_PATH,
                    "text_validation_report",
                ),
                _output_record(
                    temporary,
                    _VALIDATION_MARKDOWN_PATH,
                    "text_validation_report",
                ),
            )
        )
        if synthesis_run is not None:
            outputs.extend(
                (
                    _output_record(
                        temporary,
                        _SOURCE_ARTICLE_MODEL_PATH,
                        "source_article_model",
                    ),
                    _output_record(
                        temporary,
                        _SYNTHESIS_RUN_PATH,
                        "synthesis_run",
                    ),
                )
            )

        manifest = deepcopy(source_manifest)
        manifest["manifest_version"] = (
            TEXT_SYNTHESIZED_MANIFEST_VERSION
            if synthesis_run is not None
            else TEXT_REVIEWED_MANIFEST_VERSION
        )
        manifest["outputs"] = sorted(outputs, key=lambda item: item["path"])
        manifest["reader"] = {
            "contract_version": reader_value["contract_version"],
            "path": _READER_PATH,
            "sha256": sha256_file(reader_path),
            "article_path": _ARTICLE_PATH,
            "article_sha256": reader_value["article"]["sha256"],
            "anchor_contract": reader_value["article"]["anchor_contract"],
        }
        manifest["article_model"] = {
            "contract_version": reviewed_model["contract_version"],
            "path": _MODEL_PATH,
            "sha256": sha256_file(model_path),
        }
        manifest["text_review"] = {
            "task_contract_version": task["contract_version"],
            "review_contract_version": review["contract_version"],
            "task_path": _TASK_PATH,
            "task_sha256": sha256_file(task_path),
            "review_path": _REVIEW_PATH,
            "review_sha256": sha256_file(review_path),
            "source_article_model_sha256": task["article_model"]["sha256"],
            "parent_manifest_sha256": sha256_file(parent_manifest_path),
            "reviewer": review["reviewer"],
            "operation_count": len(review["operations"]),
            "validation_path": _VALIDATION_PATH,
            "validation_sha256": sha256_file(validation_path),
        }
        if synthesis_run is not None:
            manifest["synthesis_run"] = {
                "contract_version": SYNTHESIS_RUN_CONTRACT_VERSION,
                "executor_version": synthesis_run["executor_version"],
                "path": _SYNTHESIS_RUN_PATH,
                "sha256": sha256_file(synthesis_path),
                "task_path": _TASK_PATH,
                "task_sha256": sha256_file(task_path),
                "review_path": _REVIEW_PATH,
                "review_sha256": sha256_file(review_path),
                "source_article_model_path": _SOURCE_ARTICLE_MODEL_PATH,
                "source_article_model_sha256": sha256_file(
                    source_model_path
                ),
            }
        validate_manifest(manifest)
        _write_text(
            temporary,
            _MANIFEST_PATH,
            canonical_manifest_json(manifest),
        )
        validate_text_reviewed_package(temporary)
        if validation_markdown_path.stat().st_size <= 0:
            raise ContractValidationError("文本复核验证报告为空")
        if target.exists():
            raise OutputConflictError("原子提交前发现输出目录已存在")
        os.replace(temporary, target)
        return TextPackageResult(target, manifest, len(review["operations"]))
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
