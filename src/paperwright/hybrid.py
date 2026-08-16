"""Single-run orchestration contract for the PaperWright hybrid pipeline.

The core owns checkpoints, identity binding, and output verification.  Model
providers remain replaceable resolvers: they receive a prepared review bundle
and must produce only artifacts accepted by the existing deterministic
validators.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping

from .api import PaperWright
from .exceptions import (
    BackendExecutionError,
    ContractValidationError,
    OutputConflictError,
    PathSafetyError,
)
from .manifest import (
    TEXT_REVIEWED_MANIFEST_VERSION,
    TEXT_SYNTHESIZED_MANIFEST_VERSION,
    sha256_file,
    validate_manifest,
)
from .paths import validate_input_pdf
from .text_package import validate_text_reviewed_package


HYBRID_RUN_CONTRACT_VERSION = "paperwright-hybrid-run-v0.1"
HYBRID_RUN_FILENAME = "run.json"
HYBRID_STAGE_NAMES = ("evidence", "resolution", "verification")
HYBRID_STAGE_STATES = {"pending", "running", "waiting", "completed", "failed"}
HYBRID_RUN_STATES = {"running", "awaiting_input", "completed", "failed"}


@dataclass(frozen=True)
class HybridResolverRequest:
    input_pdf: Path
    review_dir: Path
    output_dir: Path
    reviewed_output_dir: Path
    evidence_level: str
    references_mode: str
    extraction_profile: str


@dataclass(frozen=True)
class HybridRunResult:
    run_dir: Path
    state: dict[str, Any]
    active_output_dir: Path | None


HybridResolver = Callable[[HybridResolverRequest], None]


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_hybrid_run(value: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "run_id",
        "pipeline",
        "source",
        "destinations",
        "configuration",
        "status",
        "current_stage",
        "stages",
        "artifacts",
        "next_action",
        "error",
        "result",
    }
    if set(value) != required:
        raise ContractValidationError("hybrid run 顶层字段不完整或包含未知字段")
    if value["contract_version"] != HYBRID_RUN_CONTRACT_VERSION:
        raise ContractValidationError("hybrid run 契约版本不受支持")
    if not _is_sha256(value["run_id"]):
        raise ContractValidationError("hybrid run_id 非法")
    if value["pipeline"] != "hybrid":
        raise ContractValidationError("hybrid pipeline 标识非法")

    source = value["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"path", "sha256"}
        or not isinstance(source["path"], str)
        or not source["path"]
        or not _is_sha256(source["sha256"])
    ):
        raise ContractValidationError("hybrid source 记录非法")
    destinations = value["destinations"]
    if (
        not isinstance(destinations, dict)
        or set(destinations)
        != {"run_dir", "review_dir", "output_dir", "reviewed_output_dir"}
        or any(not isinstance(item, str) or not item for item in destinations.values())
    ):
        raise ContractValidationError("hybrid destinations 非法")
    configuration = value["configuration"]
    if (
        not isinstance(configuration, dict)
        or set(configuration)
        != {
            "backend",
            "extraction_profile",
            "preview_scale",
            "layout_protocol",
            "evidence_level",
            "references_mode",
        }
        or configuration["backend"] not in {"pdfium", "pdfbox"}
        or configuration["extraction_profile"]
        not in {"fast", "standard", "forensic"}
        or not isinstance(configuration["preview_scale"], (int, float))
        or isinstance(configuration["preview_scale"], bool)
        or configuration["preview_scale"] <= 0
        or configuration["layout_protocol"] != "candidate-relations-v0.1"
        or configuration["evidence_level"] not in {"minimal", "standard", "full"}
        or configuration["references_mode"] not in {"keep", "omit", "separate"}
    ):
        raise ContractValidationError("hybrid configuration 非法")

    if value["status"] not in HYBRID_RUN_STATES:
        raise ContractValidationError("hybrid status 非法")
    current_stage = value["current_stage"]
    if current_stage is not None and current_stage not in HYBRID_STAGE_NAMES:
        raise ContractValidationError("hybrid current_stage 非法")
    stages = value["stages"]
    if not isinstance(stages, list) or len(stages) != len(HYBRID_STAGE_NAMES):
        raise ContractValidationError("hybrid stages 不完整")
    for expected_name, stage in zip(HYBRID_STAGE_NAMES, stages, strict=True):
        if (
            not isinstance(stage, dict)
            or set(stage) != {"name", "status", "attempts"}
            or stage["name"] != expected_name
            or stage["status"] not in HYBRID_STAGE_STATES
            or not isinstance(stage["attempts"], int)
            or isinstance(stage["attempts"], bool)
            or stage["attempts"] < 0
        ):
            raise ContractValidationError("hybrid stage 记录非法")

    artifacts = value["artifacts"]
    if not isinstance(artifacts, list):
        raise ContractValidationError("hybrid artifacts 必须是数组")
    roles: set[str] = set()
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"role", "path", "sha256"}
            or not isinstance(artifact["role"], str)
            or not artifact["role"]
            or artifact["role"] in roles
            or not isinstance(artifact["path"], str)
            or not artifact["path"]
            or not _is_sha256(artifact["sha256"])
        ):
            raise ContractValidationError("hybrid artifact 记录非法")
        roles.add(artifact["role"])

    next_action = value["next_action"]
    if next_action is not None and (
        not isinstance(next_action, dict)
        or set(next_action) != {"kind", "message", "path"}
        or next_action["kind"] not in {"confirm_content_roi", "provide_resolver"}
        or not isinstance(next_action["message"], str)
        or not next_action["message"]
        or not isinstance(next_action["path"], str)
        or not next_action["path"]
    ):
        raise ContractValidationError("hybrid next_action 非法")
    error = value["error"]
    if error is not None and (
        not isinstance(error, dict)
        or set(error) != {"stage", "type", "message"}
        or error["stage"] not in HYBRID_STAGE_NAMES
        or not all(isinstance(error[key], str) and error[key] for key in ("type", "message"))
    ):
        raise ContractValidationError("hybrid error 非法")
    result = value["result"]
    if result is not None and (
        not isinstance(result, dict)
        or set(result) != {"active_output_dir", "manifest_version", "manifest_sha256"}
        or not isinstance(result["active_output_dir"], str)
        or not result["active_output_dir"]
        or not isinstance(result["manifest_version"], str)
        or not result["manifest_version"]
        or not _is_sha256(result["manifest_sha256"])
    ):
        raise ContractValidationError("hybrid result 非法")
    if value["status"] == "completed" and (current_stage is not None or result is None):
        raise ContractValidationError("completed hybrid run 缺少最终结果")
    if value["status"] == "awaiting_input" and next_action is None:
        raise ContractValidationError("awaiting_input hybrid run 缺少下一动作")
    if value["status"] == "failed" and error is None:
        raise ContractValidationError("failed hybrid run 缺少错误")


def canonical_hybrid_run_json(value: Mapping[str, Any]) -> str:
    validate_hybrid_run(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


def _atomic_write_run(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_hybrid_run_json(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _stage(state: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in state["stages"] if item["name"] == name)


def _set_stage(
    state: dict[str, Any],
    name: str,
    status: str,
    *,
    increment_attempt: bool = False,
) -> None:
    stage = _stage(state, name)
    stage["status"] = status
    if increment_attempt:
        stage["attempts"] += 1
    state["current_stage"] = name if status != "completed" else state["current_stage"]


def _record_artifact(
    state: dict[str, Any],
    *,
    role: str,
    path: Path,
) -> None:
    record = {"role": role, "path": str(path), "sha256": sha256_file(path)}
    state["artifacts"] = [
        item for item in state["artifacts"] if item["role"] != role
    ]
    state["artifacts"].append(record)
    state["artifacts"].sort(key=lambda item: item["role"])


def _verify_run_artifacts(state: Mapping[str, Any]) -> None:
    for artifact in state["artifacts"]:
        path = Path(artifact["path"])
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise ContractValidationError(
                f"hybrid run 产物缺失或哈希不匹配: {artifact['role']}"
            )


def _verify_package(root: Path, source_sha256: str) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        manifest_path = root / "_paperwright" / "manifest.json"
    if not manifest_path.is_file():
        raise BackendExecutionError(f"hybrid 输出缺少 manifest: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    if manifest["source_sha256"] != source_sha256:
        raise BackendExecutionError("hybrid 输出 manifest 与输入 PDF 哈希不一致")
    if manifest["manifest_version"] in {
        TEXT_REVIEWED_MANIFEST_VERSION,
        TEXT_SYNTHESIZED_MANIFEST_VERSION,
    }:
        validate_text_reviewed_package(root)
    else:
        for record in manifest["outputs"]:
            candidate = root / record["path"]
            if (
                not candidate.is_file()
                or candidate.stat().st_size != record["size_bytes"]
                or sha256_file(candidate) != record["sha256"]
            ):
                raise BackendExecutionError(
                    f"hybrid 输出文件缺失或哈希不匹配: {record['path']}"
                )
    return manifest


class HybridPipeline:
    """Checkpointed facade over evidence preparation and issue resolution."""

    def __init__(
        self,
        product: PaperWright,
        *,
        resolver: HybridResolver | None = None,
    ) -> None:
        self.product = product
        self.resolver = resolver

    def run(
        self,
        input_pdf: str | Path,
        output_dir: str | Path,
        *,
        run_dir: str | Path | None = None,
        reviewed_output_dir: str | Path | None = None,
        content_roi_json: str | Path | None = None,
        resume: bool = False,
        preview_scale: float = 1.5,
        extraction_profile: str = "standard",
        evidence_level: str = "standard",
        references_mode: str = "keep",
    ) -> HybridRunResult:
        source = validate_input_pdf(input_pdf)
        output = Path(output_dir).expanduser().resolve()
        run_root = (
            Path(run_dir).expanduser().resolve()
            if run_dir is not None
            else output.with_name(f"{output.name}.paperwright-run")
        )
        reviewed_output = (
            Path(reviewed_output_dir).expanduser().resolve()
            if reviewed_output_dir is not None
            else output.with_name(f"{output.name}-text-reviewed")
        )
        if len({source, output, run_root, reviewed_output}) != 4:
            raise PathSafetyError("hybrid 输入、运行目录和输出目录必须互不相同")
        if run_root in source.parents or output in source.parents:
            raise PathSafetyError("hybrid 运行/输出目录不能包含输入 PDF")
        if preview_scale <= 0:
            raise ValueError("preview_scale 必须大于 0")
        if extraction_profile not in {"fast", "standard", "forensic"}:
            raise ValueError("extraction_profile 必须是 fast/standard/forensic")
        if evidence_level not in {"minimal", "standard", "full"}:
            raise ValueError("evidence_level 必须是 minimal/standard/full")
        if references_mode not in {"keep", "omit", "separate"}:
            raise ValueError("references_mode 必须是 keep/omit/separate")

        run_path = run_root / HYBRID_RUN_FILENAME
        source_hash = sha256_file(source)
        configuration = {
            "backend": self.product.config.backend,
            "extraction_profile": extraction_profile,
            "preview_scale": preview_scale,
            "layout_protocol": "candidate-relations-v0.1",
            "evidence_level": evidence_level,
            "references_mode": references_mode,
        }
        destinations = {
            "run_dir": str(run_root),
            "review_dir": str(run_root / "layout-review"),
            "output_dir": str(output),
            "reviewed_output_dir": str(reviewed_output),
        }

        if resume:
            if not run_path.is_file():
                raise OutputConflictError(f"无法恢复：缺少 {run_path}")
            state = json.loads(run_path.read_text(encoding="utf-8"))
            validate_hybrid_run(state)
            if state["source"] != {"path": str(source), "sha256": source_hash}:
                raise ContractValidationError("恢复输入与 run.json 不一致")
            if state["destinations"] != destinations:
                raise ContractValidationError("恢复输出路径与 run.json 不一致")
            if state["configuration"] != configuration:
                raise ContractValidationError("恢复配置与 run.json 不一致")
            _verify_run_artifacts(state)
            if state["status"] == "completed":
                active = Path(state["result"]["active_output_dir"])
                _verify_package(active, source_hash)
                return HybridRunResult(run_root, state, active)
        else:
            if run_root.exists():
                raise OutputConflictError(f"hybrid 运行目录已存在，拒绝覆盖: {run_root}")
            if output.exists() or reviewed_output.exists():
                raise OutputConflictError("hybrid 输出目录已存在，拒绝覆盖")
            run_root.parent.mkdir(parents=True, exist_ok=True)
            run_root.mkdir()
            run_id = hashlib.sha256(
                (source_hash + "\n" + str(output)).encode("utf-8")
            ).hexdigest()
            state = {
                "contract_version": HYBRID_RUN_CONTRACT_VERSION,
                "run_id": run_id,
                "pipeline": "hybrid",
                "source": {"path": str(source), "sha256": source_hash},
                "destinations": destinations,
                "configuration": configuration,
                "status": "running",
                "current_stage": "evidence",
                "stages": [
                    {"name": name, "status": "pending", "attempts": 0}
                    for name in HYBRID_STAGE_NAMES
                ],
                "artifacts": [],
                "next_action": None,
                "error": None,
                "result": None,
            }
            _atomic_write_run(run_path, state)

        try:
            review_dir = Path(destinations["review_dir"])
            evidence_stage = _stage(state, "evidence")
            if evidence_stage["status"] != "completed":
                if content_roi_json is None:
                    proposal_dir = run_root / "layout-proposal"
                    if not proposal_dir.exists():
                        _set_stage(
                            state, "evidence", "running", increment_attempt=True
                        )
                        state["status"] = "running"
                        state["next_action"] = None
                        _atomic_write_run(run_path, state)
                        self.product.prepare_layout_review(
                            source,
                            proposal_dir,
                            preview_scale=preview_scale,
                            extraction_profile=extraction_profile,
                            # Compatibility task is visual-direct; the same
                            # preparation emits the candidate-relation task
                            # used by the Hybrid resolver.
                            review_mode="visual-direct",
                        )
                    proposal_roi = proposal_dir / "content-roi.json"
                    _record_artifact(
                        state,
                        role="content_roi_proposal",
                        path=proposal_roi,
                    )
                    _set_stage(state, "evidence", "waiting")
                    state["status"] = "awaiting_input"
                    state["next_action"] = {
                        "kind": "confirm_content_roi",
                        "message": "确认 content-roi.json 后以 --resume --content-roi-json 继续",
                        "path": str(proposal_roi),
                    }
                    state["error"] = None
                    _atomic_write_run(run_path, state)
                    return HybridRunResult(run_root, state, None)

                roi_path = Path(content_roi_json).expanduser().resolve()
                confirmed_roi_path = run_root / "confirmed-content-roi.json"
                if not roi_path.is_file():
                    raise FileNotFoundError(f"确认 ROI 文件不存在: {roi_path}")
                if not confirmed_roi_path.exists():
                    with confirmed_roi_path.open("xb") as stream:
                        stream.write(roi_path.read_bytes())
                elif sha256_file(confirmed_roi_path) != sha256_file(roi_path):
                    raise OutputConflictError(
                        "run 中已绑定不同的 confirmed-content-roi.json"
                    )
                _record_artifact(
                    state,
                    role="confirmed_content_roi",
                    path=confirmed_roi_path,
                )
                _set_stage(state, "evidence", "running", increment_attempt=True)
                state["status"] = "running"
                state["next_action"] = None
                state["error"] = None
                _atomic_write_run(run_path, state)
                if not review_dir.exists():
                    self.product.prepare_layout_review(
                        source,
                        review_dir,
                        preview_scale=preview_scale,
                        content_roi_json=confirmed_roi_path,
                        extraction_profile=extraction_profile,
                        review_mode="visual-direct",
                    )
                for role, relative in (
                    ("review_index", "review-index.json"),
                    ("issue_routing", "issue-routing.json"),
                    ("compatibility_routing", "routing.json"),
                ):
                    _record_artifact(
                        state, role=role, path=review_dir / relative
                    )
                _set_stage(state, "evidence", "completed")
                state["current_stage"] = "resolution"
                _atomic_write_run(run_path, state)

            resolution_stage = _stage(state, "resolution")
            if resolution_stage["status"] != "completed":
                if self.resolver is None:
                    _set_stage(state, "resolution", "waiting")
                    state["status"] = "awaiting_input"
                    state["next_action"] = {
                        "kind": "provide_resolver",
                        "message": "准备完成；提供 resolver 后以 --resume 继续",
                        "path": str(review_dir / "issue-routing.json"),
                    }
                    state["error"] = None
                    _atomic_write_run(run_path, state)
                    return HybridRunResult(run_root, state, None)
                if output.exists() or reviewed_output.exists():
                    raise OutputConflictError(
                        "失败恢复时发现部分输出目录；为避免误判完成，请使用新的输出路径重新运行"
                    )
                _set_stage(
                    state, "resolution", "running", increment_attempt=True
                )
                state["status"] = "running"
                state["next_action"] = None
                state["error"] = None
                _atomic_write_run(run_path, state)
                self.resolver(
                    HybridResolverRequest(
                        input_pdf=source,
                        review_dir=review_dir,
                        output_dir=output,
                        reviewed_output_dir=reviewed_output,
                        evidence_level=evidence_level,
                        references_mode=references_mode,
                        extraction_profile=extraction_profile,
                    )
                )
                _set_stage(state, "resolution", "completed")
                state["current_stage"] = "verification"
                _atomic_write_run(run_path, state)

            _set_stage(state, "verification", "running", increment_attempt=True)
            state["status"] = "running"
            _atomic_write_run(run_path, state)
            active_output = reviewed_output if reviewed_output.is_dir() else output
            manifest = _verify_package(active_output, source_hash)
            manifest_path = active_output / "manifest.json"
            if not manifest_path.is_file():
                manifest_path = active_output / "_paperwright" / "manifest.json"
            _record_artifact(state, role="final_manifest", path=manifest_path)
            completeness_path = active_output / "_paperwright" / "completeness-report.json"
            if completeness_path.is_file():
                _record_artifact(
                    state, role="completeness_report", path=completeness_path
                )
            _set_stage(state, "verification", "completed")
            state["status"] = "completed"
            state["current_stage"] = None
            state["next_action"] = None
            state["error"] = None
            state["result"] = {
                "active_output_dir": str(active_output),
                "manifest_version": manifest["manifest_version"],
                "manifest_sha256": sha256_file(manifest_path),
            }
            _atomic_write_run(run_path, state)
            return HybridRunResult(run_root, state, active_output)
        except Exception as exc:
            stage_name = state.get("current_stage") or "verification"
            if stage_name not in HYBRID_STAGE_NAMES:
                stage_name = "verification"
            _set_stage(state, stage_name, "failed")
            state["status"] = "failed"
            state["next_action"] = None
            state["error"] = {
                "stage": stage_name,
                "type": type(exc).__name__,
                "message": str(exc) or type(exc).__name__,
            }
            _atomic_write_run(run_path, state)
            raise
