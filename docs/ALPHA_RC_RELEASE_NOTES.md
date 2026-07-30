# Paper2MD 0.6.0a0 Alpha RC 变更说明

本候选基于 `agent/v2-rebuild`
`47e31abb58d062e1da0ecf92a2a303afddaa39af`，用于源码审阅、本地安装和
有限试用，不是正式公开 release。

## 已包含

- `paper2md --version/--help/convert/batch/validate-model`；
- PDFium 主后端，PhysicalDocument v0.2；
- `article.md + images/ + manifest.json + physical_document.json`；
- 默认关闭、显式 opt-in 的 auto region-render；
- 非递归且确定性排序的 batch、逐文档原子输出和机器可读摘要；
- manifest v0.4/v0.5、batch summary schema 与错误分类；
- Linux 云端复核，以及 Phase 5 留存的 Windows/Python 3.11.2 独立证据。

## 默认与兼容性

- 默认 backend：`pdfium`；
- region-render 默认：`off`，`auto` 必须明确启用；
- PDFBox 只是接口边界，选择后明确返回 unavailable；
- region-render off 继续生成 manifest v0.4；explicit/auto 使用 v0.5；
- PhysicalDocument 契约保持 v0.2。

升级前请阅读
[`MANIFEST_MIGRATION_V0.4.md`](MANIFEST_MIGRATION_V0.4.md) 和
[`MANIFEST_MIGRATION_V0.5.md`](MANIFEST_MIGRATION_V0.5.md)。

## 明确不包含

OCR/扫描 PDF 识别、语义表格、公式 LaTeX、完整 PDFBox 后端、GUI、
服务器/API、容器、公开 PyPI、签名、tag 或二进制发布。

Paper2MD 不调用生成式 AI、LLM API、云 OCR 或外部推理服务。

## 许可证状态

项目级许可证仍为 `NOASSERTION`；`agg23` 和 PDFium bundled notices 仍需
发布级审查。因此：

- 可以继续仓库内源码研发和项目所有者控制下的本地试用；
- 本 source-only 候选可供项目所有者/审查者内部交付；
- 不批准向公众再分发源码包、wheel/sdist 或包含 PDFium 的二进制。
