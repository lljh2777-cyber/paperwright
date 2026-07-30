# Paper2MD Phase 4 通用 auto region-render 报告

## 结论

阶段自检结论为 `PHASE4_PASS_WITH_LIMITATIONS`。默认 `off` 模式对 8 篇
RW2 输出保持逐文件字节一致；显式 opt-in 的 `auto` 模式在 138 页中保守
批准 2 个候选，并对这 2 个候选全部做了人工并排视觉检查。RW2-005 p3 与
RW2-007 p5 均完整包含原生位图及缺失的矢量内容，未烧入 caption、未包含
明显无关正文。RW2-005 p7 继续因 `continued on next page` 安全拒绝。

该结论只说明当前 8 篇 born-digital OA 小样本上的保守可用性，不代表真实
出版商总体泛化，也不构成 Alpha、分发或许可证批准。

## 实现

- `RegionRenderPolicy` 提供 `off`、`explicit`、`auto`；默认仍为 `off`。
- 自动候选只用同页 Figure group、显式 caption、image/vector/text bbox、
  native 不完整证据及固定阈值，不使用论文 ID、固定页码或标题关键词。
- 跨页 continuation、caption 缺失/歧义/竞争、近整页、越界、正文/页眉/
  页脚侵入、低方差、像素上限和来源 hash 不符均拒绝。
- 首次视觉检查发现 RW2-007 p6/p8 为局部裁剪。保留
  `auto-final-v1` 后，在修复后运行前冻结 v1.1 安全补充规则：候选未覆盖
  显式 caption 水平跨度超过 15% 时拒绝。该规则不含样本特判。
- 通过后输出 `manifest v0.5` 的 `region_render_policy`、候选 bbox、DPI、
  像素、渲染器、source hash、caption 和 vector evidence；native asset
  始终保留。默认关闭仍输出 v0.4。

核心代码位于：

- `src/paper2md/region_render.py`
- `src/paper2md/writer.py`
- `src/paper2md/config.py`
- `src/paper2md/manifest.py`
- `src/paper2md/schemas/manifest.schema.json`

## 测试与真实运行

- 单元/回归测试：77/77，其中旧测试 60、新增 17；0 failure、0 skip。
- 自生成 fixture 覆盖完整位图、纯矢量诚实拒绝、混合、多面板、相邻双栏、
  caption 歧义、continued、近整页、正文侵入、caption 水平范围不足、
  rotation、候选上限、schema、Markdown 邻接和逐字节确定性；既有 backend
  测试继续覆盖空白、越界、caption guard、像素上限和 source hash。
- 默认回归：8/8 与提交 `25e4ecea` 的权威输出逐文件字节一致，manifest
  保持 v0.4，region render 为 0。
- auto：8/8、138 页、127 个现场文件、153,763,428 bytes；2 rendered、
  31 figure-level rejected、17 global rejected、39 degraded；0 failure、
  0 timeout、0 skip。
- 双轮确定性：RW2-001、RW2-002、RW2-005、RW2-007 共 4/4 输出树逐文件
  hash 一致，覆盖全部发生 render 的论文。
- 12/12 机器硬检查通过，asset/hash/page/bbox、native retention、caption
  邻接和三个已知目标均一致。

逐篇 rendered 数：

| 论文 | rendered | 说明 |
|---|---:|---|
| RW2-001 | 0 | 保守拒绝/非候选 |
| RW2-002 | 0 | 保守拒绝/非候选 |
| RW2-003 | 0 | 保守拒绝/非候选 |
| RW2-004 | 0 | 保守拒绝/非候选 |
| RW2-005 | 1 | p3 通过；p7 continued 拒绝 |
| RW2-006 | 0 | 保守拒绝/非候选 |
| RW2-007 | 1 | p5 通过；p6/p8 修复后拒绝局部裁剪 |
| RW2-008 | 0 | 无需/不满足保守证据 |

## 视觉结论

最终获准候选 2/2 全检：

- RW2-005 p3：a-d 面板完整，位图与矢量散点均可见；caption 在 Markdown
  中紧邻但未烧入图；PASS。
- RW2-007 p5：a-e 面板完整，组织位图、热图、散点图和条形图可见；
  caption 未烧入；PASS。
- RW2-005 p7：页底明确出现 continued 文本；不生成 region asset；
  PASS_SAFE_REJECTION。

视觉 PNG 单独进入 `phase4-auto-region-visual-evidence.zip`，不提交 Git。
source-only 包只保留 `visual_review_inventory.json` 和哈希化机器事实。

## 限制

- auto 默认关闭；纯矢量且无 native Figure group 的页面继续拒绝。
- 保守规则会漏检，尤其 caption bbox 与真实 Figure 横向范围关系弱、
  rotation 几何无法充分验证或缺少清晰 native group 的页面。
- 不做 OCR、扫描 PDF、语义表格、公式 LaTeX、深层 caption/Figure 语义、
  完整 PDFBox 评分或发布打包。
- `agg23=NOASSERTION` 仍只阻断正式二进制分发批准，不阻断本地源码研发。
- 首次分析器命令因遗漏 `PYTHONPATH=src` 退出 1；未产生可信结论，已在
  `test_summary.json` 披露。pre-fix 两个视觉误裁剪也完整披露。

## 后续建议

本地审查应先应用 patch，在锁定依赖下复跑 77 项测试、fixture、summary、
repo policy 和 fresh-tree 检查，并独立查看视觉 evidence ZIP。通过前不得
扩大默认启用范围；下一阶段若继续，应优先补充纯矢量 group 边界和更多真实
版式的保守拒绝校准，而不是放宽当前安全阈值。
