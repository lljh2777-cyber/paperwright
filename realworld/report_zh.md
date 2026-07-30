# Paper2MD v2 Stage C 真实论文验证报告

## 结论

`PASS_WITH_LIMITATIONS`

在 8 篇新选择、许可证明确的 OA born-digital 科研 PDF（4 类出版来源、
138 页）上，当前 PDFium MVP 全部完成转换。最终结果为：标题 8/8
规范化精确匹配、8/8 无非空整页丢失、97/97 图片文件/引用/hash/尺寸可
验证、39 个表格相关页诚实 degraded、4/4 双轮逐文件确定、0 skip。

这不是旧 Phase 1B/2 检查点的恢复，也不是发布批准或全面出版商泛化。

## 冻结输入与权利

权威记录为 `realworld/oa_sources.json`。8 篇均记录落地页、最终 PDF URL、
许可证证据 URL、下载 UTC、SHA-256、字节数、页数与版式特征；均为
CC BY 4.0。PDF 和转换输出仅留在 Work 云端临时测试现场，不进入 Git/
source-only 交付。

来源分布：

- PLOS：RW2-001、RW2-002
- Frontiers：RW2-003、RW2-004
- eLife：RW2-005、RW2-006
- Nature Communications：RW2-007
- mSystems（PMC/Europe PMC）：RW2-008

输入共 45,206,996 bytes、138 页、8 个唯一 SHA-256。

已披露取得异常：

- RW2-001 首次传输在 6,230,016 bytes 截断，被 pdfinfo/PDFium 拒绝；
  后续重新下载到 `.part`，按 Content-Length、页数和冻结 hash 验证后
  原子落盘。
- RW2-008 的 NCBI OA API 证明 CC BY，但历史 FTP package 返回 404；
  最终改用权威 Europe PMC PDF endpoint，并冻结实际结果 hash。

## baseline 暴露的共性问题

1. eLife 两篇 PDF 元数据 Title 缺失，baseline 分别误选
   `TOOLS AND RESOURCES`、`RESEARCH ARTICLE`；mSystems 元数据为
   `SM-MSYS210386 1..6`。
2. 某些 PDF 把一行拆成逐词 text objects。旧几何算法把词的 x 位置当成
   两栏，导致标题/正文词序破碎。
3. RW2-003 第 2 页真实双栏仅约 20pt gutter，第一次修复的 30pt 同行
   阈值会把左右栏同高文字合并。该 pre-fix 失败被保留并绑定回归。

## 最小产品修复

代码只改动：

- `src/paper2md/backends/pdfium.py`
  - 先保守形成同行，再做列排序；
  - 同行 gap 上限 14pt，wide-line 阈值 65% 页宽；
  - 为元素增加确定性的 `line_group`/`line_position` 元数据。
- `src/paper2md/writer.py`
  - 验证/拒绝通用或错误 PDF Title；
  - 从首页 bbox 高度和连续多行选择标题；
  - Markdown 合并同行碎片并做 NFC/C0 清理；
  - 原始控制字符继续保留在 PhysicalDocument，并在 manifest 披露数量。

未修改 PDFium 底层解析、图片解码、表格、caption、OCR 或公式。

## 机器结果

权威机器摘要：`realworld/realworld_summary.json`。

| 指标 | 结果 |
|---|---:|
| 转换成功 | 8/8 |
| 标题规范化精确匹配 | 8/8 |
| 无非空整页丢失 | 8/8 |
| 字符多重集逐篇 macro 平均 | 0.996898 |
| 全部页面最差字符召回 | 0.965909 |
| 图片完整性 | 97/97 |
| degraded 表格页 | 39 |
| 伪造 Markdown 表格网格 | 0 |
| 双轮逐文件确定性 | 4/4 |
| 最终产品输出 | 121 files / 149,265,655 bytes |
| failure / timeout / skip | 0 / 0 / 0 |

输出体积主要由 RW2-007 的 78,123 个矢量对象对应 PhysicalDocument/
manifest 明细造成；不应把它解释为 78,123 张 Figure。

## 逐篇摘要

| ID | 页 | 标题 | 图片 | degraded页 | 结果与主要限制 |
|---|---:|---|---:|---:|---|
| RW2-001 | 29 | 精确 | 9 | 15 | 主体完整；页眉页脚和小标志保留 |
| RW2-002 | 25 | 精确 | 7 | 2 | 同行改善；作者上标局部错序 |
| RW2-003 | 10 | 精确 | 9 | 2 | 第2页双栏由交错修为列优先；词内空格残留 |
| RW2-004 | 4 | 精确 | 1 | 3 | 真实大表只保留文本/degraded |
| RW2-005 | 18 | baseline错→精确 | 43 | 1 | 多面板图明显碎片化 |
| RW2-006 | 30 | baseline错→精确 | 10 | 8 | 大图有效；第22页小图标噪声 |
| RW2-007 | 16 | 精确 | 6 | 5 | 内容完整；矢量追溯使输出约78.9MB |
| RW2-008 | 6 | baseline错→精确 | 12 | 3 | 双栏可读；重复期刊标志/作者上标噪声 |

详细自动/视觉证据见 `realworld/manual_review_zh.md`。

## 测试

最终 source-only 测试由 `tools/run_stage_c_checks.py` 生成：

- 43/43 单元测试；
- 13/13 自生成 PDF 内容断言；
- fixture check、compileall、repo policy、diff check；
- Stage C 持久摘要的 12 项一致性检查；
- 损坏 PDF、已有输出、输入输出冲突/workspace escape 安全回归；
- 新增同行碎片、多行标题、错误元数据、C0 清理和 20pt 双栏 gutter
  回归。

新源码包还会在从基线 `0897f3c` 创建的全新临时 worktree 中应用 patch，
重新运行不依赖真实 PDF 的完整测试。

## 许可证与边界

- 运行时固定为 pypdfium2 5.3.0 / PDFium 145.0.7616.0 / Pillow 12.2.0。
- 实际代码没有 LLM/API/云 OCR/生成式模型，也没有 PDFBox 能力评分。
- 8 篇 OA 论文及其图片/转换输出不进入源码交付。
- `agg23=NOASSERTION` 仍只阻断正式二进制分发批准；本阶段不批准分发。

## 已知限制与下一步

1. P1：Figure 对象聚合/区域渲染，优先解决 RW2-005 多面板碎片化。
2. P1：确定性 caption 配对与 Markdown 邻接，拒绝不确定/跨页强配。
3. P2：跨页重复页眉页脚抑制（保留 manifest provenance）。
4. P2：作者上标、词内空格和参考文献局部列序。
5. P2：矢量对象清单压缩/索引，避免 RW2-007 类输出膨胀。

下一阶段必须等待本地审查并给出 Stage C commit SHA；本报告不自动放行
Phase 3、Alpha 打包或发布。
