# Paper2MD 0.7.0a0 Source Alpha 变更说明

本版本在 0.6 Alpha 直接转换基线上增加混合科学 PDF 布局流程，用于源码审阅、
本地安装和有限试用，不是正式稳定 release。

## 已包含

- `paper2md --version/--help/convert/batch/validate-model`；
- `layout-prepare/validate-layout-task/validate-final-layout/layout-apply`；
- `benchmark-extract/layout-export-dataset`；
- PDFium 主后端，PhysicalDocument v0.2；
- `article.md + images/ + manifest.json + physical_document.json`；
- 默认关闭、显式 opt-in 的 auto region-render；
- 非递归且确定性排序的 batch、逐文档原子输出和机器可读摘要；
- Content ROI、规则/栅格候选、结构化布局复核和严格应用；
- `fast`、选择性升级的 `standard` 和完整对象 `forensic` 提取；
- manifest v0.4/v0.5/v0.7、证据包、质量报告和训练数据导出；
- Linux 云端复核，以及 Phase 5 留存的 Windows/Python 3.11.2 独立证据。

## 默认与兼容性

- 默认 backend：`pdfium`；
- region-render 默认：`off`，`auto` 必须明确启用；
- PDFBox 只是接口边界，选择后明确返回 unavailable；
- region-render off 继续生成 manifest v0.4；explicit/auto 使用 v0.5；
- PhysicalDocument 契约保持 v0.2。
- 当前混合布局输出使用 manifest v0.7，继续接受旧 v0.6；
- 包版本和各数据契约独立演进，不要求数字相同。

升级前请阅读
[`MANIFEST_MIGRATION_V0.4.md`](MANIFEST_MIGRATION_V0.4.md) 和
[`MANIFEST_MIGRATION_V0.5.md`](MANIFEST_MIGRATION_V0.5.md)。

## 明确不包含

OCR/扫描 PDF 识别、语义表格、公式 LaTeX、完整 PDFBox 后端、GUI、
服务器/API、容器、公开 PyPI、签名、tag 或二进制发布。

Paper2MD 不调用生成式 AI、LLM API、云 OCR 或外部推理服务。

## 许可证与第三方依赖

项目源码采用 Apache License 2.0，分发源码或衍生作品时应保留 `LICENSE` 和
`NOTICE`。PDFium/pypdfium2 bundled dependencies 的 notices 属于独立的第三方
分发审计事项；尤其是在重新分发二进制运行时或包含它们的 wheel 前，应按目标
制品重新核对。
