# Paper2MD 0.8.0a0 Source Alpha 变更说明

本版本在混合科学 PDF 布局流程上增加 Reader 互操作契约，用于源码审阅、本地
安装和有限试用，不是正式稳定 release。

## 已包含

- `paper2md --version/--help/convert/batch/validate-model/validate-reader`；
- `text-prepare/validate-text-task/validate-text-review/text-apply`；
- `layout-prepare/validate-layout-task/validate-final-layout/layout-apply`；
- `benchmark-extract/layout-export-dataset`；
- PDFium 主后端，PhysicalDocument v0.2；
- `article.md + images/ + manifest.json + physical_document.json`；
- 默认关闭、显式 opt-in 的 auto region-render；
- 非递归且确定性排序的 batch、逐文档原子输出和机器可读摘要；
- Content ROI、规则/栅格候选、结构化布局复核和严格应用；
- `fast`、选择性升级的 `standard` 和完整对象 `forensic` 提取；
- manifest v0.4/v0.5/v0.8、Reader v0.1、证据包、质量报告和训练数据导出；
- 稳定 `p2md:block` / `p2md:slot` 锚点、Figure/图注关系与内容指纹；
- Text Task v0.1 与 Text Review v0.1 的源保持文本整理边界；
- Linux 云端复核，以及 Phase 5 留存的 Windows/Python 3.11.2 独立证据。

## 默认与兼容性

- 默认 backend：`pdfium`；
- region-render 默认：`off`，`auto` 必须明确启用；
- PDFBox 只是接口边界，选择后明确返回 unavailable；
- region-render off 继续生成 manifest v0.4；explicit/auto 使用 v0.5；
- PhysicalDocument 契约保持 v0.2。
- 当前混合布局输出使用 manifest v0.9 和 Article Model v0.1，继续接受旧
  manifest v0.6–v0.8；
- Reader v0.1 是所有混合布局证据级别的功能索引，不随 minimal/standard/full
  被裁剪；
- 包版本和各数据契约独立演进，不要求数字相同。

升级前请阅读
[`MANIFEST_MIGRATION_V0.4.md`](MANIFEST_MIGRATION_V0.4.md) 和
[`MANIFEST_MIGRATION_V0.5.md`](MANIFEST_MIGRATION_V0.5.md)，以及
[`MANIFEST_MIGRATION_V0.8.md`](MANIFEST_MIGRATION_V0.8.md)。

## 明确不包含

OCR/扫描 PDF 识别、语义表格、公式 LaTeX、完整 PDFBox 后端、GUI、
服务器/API、容器、公开 PyPI、签名、tag 或二进制发布。

Paper2MD 不调用生成式 AI、LLM API、云 OCR 或外部推理服务。

## 当前验证记录

2026-08-03 在 Windows / Python 3.11.2、`pypdfium2==5.11.0`、
PDFium 151.0.7920.0 和 `Pillow==12.2.0` 的验证环境中：

- 264/264 单元测试通过；
- 内容 smoke 13/13 通过，包含双轮逐文件确定性检查；
- wheel/sdist 两种安装各 6 项命令检查通过，输出树一致；
- fixture、字节码编译、仓库策略和 `git diff --check` 通过。

## 许可证与第三方依赖

项目源码采用 Apache License 2.0，分发源码或衍生作品时应保留 `LICENSE` 和
`NOTICE`。PDFium/pypdfium2 bundled dependencies 的 notices 属于独立的第三方
分发审计事项；尤其是在重新分发二进制运行时或包含它们的 wheel 前，应按目标
制品重新核对。
