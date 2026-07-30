# Paper2MD Phase 3 Figure/Caption 增强报告

## 结论

`PHASE3_PASS_WITH_LIMITATIONS`

从 Stage C 权威提交
`8ecd01871eff02e700f0cef1c64cae186be8c69f` 出发，Phase 3 已实现可调用
的同页 Figure 分组、保守 caption 配对和 Markdown 局部邻接，并保持
Stage C 标题、正文、双栏和表格诚实降级行为。

8 篇冻结 OA 论文共 138 页全部转换；标题 8/8 规范化精确，PhysicalDocument
8/8 与 Stage C 字节一致，说明本轮没有改写底层提取或全局阅读顺序。
97 个原生图片对象形成 41 个 Figure group，其中 4 个是多对象组合；
caption 状态为 matched 27、ambiguous 3、none 11。全部 27 个 matched
association 通过同页、element ID、bbox、text hash 和 Markdown 前置邻接
机器检查；人工视觉检查覆盖 8 篇各一个主 Figure，结果 7 pass、1 partial。

## 冻结输入与环境

- 源码基线与逐文件 hash：`baseline_source_hashes.json`；
- Figure/Caption 规则：`frozen_rules_v1.json`；
- 8 类自生成 gold：`fixtures/figure_caption_cases.json/.csv`；
- OA 输入权威记录：`realworld/oa_sources.json`，8 个现场 PDF 的
  SHA-256、字节数、页数均重新匹配；
- Python 3.12.13、pypdfium2 5.3.0、PDFium 145.0.7616.0、
  Pillow 12.2.0；
- `libpdfium.so` SHA-256
  `504df0960b4fab9e7c3bce8e4cf944d072a5aba76a5a199609d7addc49656568`。

没有下载新论文、没有调用 LLM/API/云 OCR，也没有运行 PDFBox 评分。

## 产品实现

- `src/paper2md/figures.py`：page-local marker 与几何分组；
- `src/paper2md/writer.py`：原始资产保留、组合 PNG、caption 邻接和
  页末降级；
- `src/paper2md/manifest.py` 与 schema：manifest v0.3；
- `docs/MANIFEST_MIGRATION_V0.3.md`：兼容与迁移边界。

组合图只按原生位图和 PDF bbox 合成。矢量对象仍在 PhysicalDocument 中
逐项保留，并在 Figure 记录中给出 count/ID sample/hash；未渲染矢量时
`rendered_into_asset=false`，禁止冒充完整 Figure。

## 机器结果

| 指标 | 结果 |
|---|---:|
| 论文 / 页 | 8 / 138 |
| 标题规范化精确 | 8/8 |
| Stage C PhysicalDocument 未变 | 8/8 |
| 原生图片对象 | 97 |
| Figure group | 41 |
| 多对象 grouped | 4 |
| 完整原生位图组 | 25 |
| fragmented/degraded 组 | 16 |
| caption matched / ambiguous / none | 27 / 3 / 11 |
| 过滤小对象/logo | 31 |
| degraded 表格页 | 39 |
| 空白/恒定像素 Figure | 0 |
| 追溯/输出 hash 错误 | 0 / 0 |
| 双轮确定性 | 4/4 |
| failure / timeout / skip | 0 / 0 / 0 |
| run1 输出 | 125 files / 152,588,151 bytes |

## 逐篇结果

| ID | 页 | 原图对象 | Figure组 | grouped | caption M/A/N | 视觉主图 | 主要限制 |
|---|---:|---:|---:|---:|---|---|---|
| RW2-001 | 29 | 9 | 7 | 0 | 4/0/3 | pass | 3 个无 caption；正文子图引用已拒绝 |
| RW2-002 | 25 | 7 | 6 | 0 | 5/1/0 | pass | 1 个歧义；caption 同行碎片仍有格式空格 |
| RW2-003 | 10 | 9 | 9 | 0 | 7/1/1 | pass | 2 个保守降级 |
| RW2-004 | 4 | 1 | 1 | 0 | 1/0/0 | pass | 表格继续 degraded |
| RW2-005 | 18 | 43 | 7 | 4 | 2/0/5 | partial | 混合矢量/位图 Figure 仍不完整 |
| RW2-006 | 30 | 10 | 4 | 0 | 3/1/0 | pass | 6 个小对象/logo 被过滤 |
| RW2-007 | 16 | 6 | 5 | 0 | 3/0/2 | pass | 纯矢量对象清单仍使输出较大 |
| RW2-008 | 6 | 12 | 2 | 0 | 2/0/0 | pass | 10 个页眉 logo/小对象被过滤 |

M/A/N 分别为 matched / ambiguous / none。

## RW2-005 改善与失败证据

第 3 页 Stage C 把 14 个对象逐个放到页末。Phase 3 将其中 12 个有明确
几何关系的位图组合成一个资产并放到 Figure 1 caption 前，另外两个 plot
保持独立对象；因此碎片化有可见改善，但尚未形成完整单一 Figure。第 7
页仍拆成三个组，caption 距离超过冻结阈值，系统拒绝强配。两处都在
manifest 中保留成员、bbox、过滤/降级原因和矢量 evidence，未用文件数量
变化冒充质量通过。

## 测试与人工检查

- 旧 43/43 测试通过；
- 总计 48/48 单元测试；
- 新增 8 类冻结 case：单图、多面板、相邻两图、双栏 caption、歧义、
  无 caption、跨页拒配、caption 周边正文顺序；
- 源码检查 8/8，0 failure / 0 skip；
- 4 篇双轮共 102 个输出文件逐文件 hash 一致；
- 人工接触图 9 个（8 主样本 + RW2-005 失败样例），未观察到空白图、
  整页截图冒充 Figure 或跨页误配。

详见 `test_report_zh.md`、`test_summary.json`、
`manual_visual_review_zh.md` 和 `visual_review.json`。

## 已知限制

1. RW2-005 混合 Figure 尚需区域 render fallback 或更强的无语义几何
   聚合；当前 4 个 grouped 资产均正确标注未渲染矢量 evidence。
2. 27 个 matched association 中只有 8 个主样本逐一视觉确认；真实
   caption gold 仍不足。
3. 页眉页脚重复抑制没有在本轮实现，避免扩大正文误删风险。
4. 不支持 OCR、扫描 PDF、语义表格、公式 LaTeX、纯矢量 Figure、
   深层 Figure 语义或跨页配对。
5. 结果只覆盖冻结 8 篇 OA 论文，不代表全部真实出版商。
6. `agg23=NOASSERTION` 继续只阻断正式二进制分发批准，不阻断本次源码研发。

Phase 3 完成后停止；本报告不进入 Alpha、发布打包或下一阶段。
