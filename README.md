# Paper2MD

Paper2MD 是一个面向科研论文的本地 PDF 转 Markdown 项目，目标是在不调用生成式 AI 或云端 OCR 的前提下，生成结构化、可复现的 Markdown 阅读层。

## v2 重建说明

此前的临时 Work 执行环境及其阶段产物已被平台清理。本仓库用于新的 v2 重建流程；新结果不得被表述为原 Phase 1B / Phase 2 检查点的恢复结果。

计划中的最小流水线：

```text
PDF → PhysicalDocument → article.md + images/ + manifest.json
```

MVP 暂定使用 PDFium 作为主后端，PDFBox 作为对照或回退。

## 仓库存储边界

仓库可以保存：

- 源码与测试
- 小型自生成 fixtures
- schema 与配置
- 开放获取论文的来源、许可证和哈希清单
- 机器可读摘要、人工检查结果、复现命令与文档

仓库不保存：

- 论文 PDF
- 大型转换输出
- PDFium、JAR 或其他二进制文件
- 凭据或令牌

后续产品开发应在评审分支中进行，不直接修改 `main`。
