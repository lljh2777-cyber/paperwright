# Hybrid Layout Stage E 真实论文验证

状态：通过候选生成阶段，允许进入训练数据导出阶段。这里的“通过”只表示
候选区块足以交给视觉 AI 复核，不表示规则已经独立完成最终版面理解。

## 验证范围

本次只使用用户本地已有的两篇 born-digital PDF，没有联网下载样本，也没有
调用 OCR、LLM 或外部 API。

| 样本 | SHA-256 | 页数 | 候选区块 | 分隔带 | 外围候选 | 混合候选 |
|---|---|---:|---:|---:|---:|---:|
| Topological domains in mammalian genomes identified by analysis of chromatin interactions | `b699e4cfe8050b7ff54382db4077916a01f2e708c670f89d75f445d018160c6f` | 5 | 41 | 37 | 17 | 4 |
| Pan-cancer spatial atlas of tertiary lymphoid | `021147f9c563de8609c31d341f54f4116cc9bed36a20793b1dbf7f2f40a54459` | 24 | 134 | 93 | 65 | 9 |

候选生成器版本：`paper2md-whitespace-candidates-v0.3`。

## 视觉检查

人工检查了 Topological 样本全部 5 页，以及 Pan-cancer 样本第 2、3、5 页。

- Topological 第 1 页：左右正文已分别形成约 42.5% 页宽的候选区块；跨栏
  细线和作者单位独立成块，不再把正文候选撑成整页宽。
- Topological 第 2 页：左右大型 Figure 均完整保留，但 Figure、caption 和
  相邻正文仍需要 AI 执行 merge/split 和顺序标注。
- Topological 第 3–5 页：双栏正文、跨栏/单栏 Figure、参考文献和外围内容均
  有候选覆盖，未发现正文被静默丢弃。
- Pan-cancer 第 2 页：标题、作者、摘要和左栏正文被规则合并为较大候选；
  这是预期的 AI split 场景。
- Pan-cancer 第 3 页：左右正文、整幅 Figure、caption 和页脚分别成块，符合
  目标结构。
- Pan-cancer 第 5 页：复杂多面板 Figure 被完整保留为一个视觉候选，不读取
  图内文字。

## 本轮修复

原算法能找到双栏空白带，但稀疏的跨栏细线或作者单位会被按中心点分配给
左栏或右栏，导致该栏候选的边界框横跨整页。

现在将真正跨越栏间空白带的稀疏对象单独分组，再对左右内容递归划分。新增
回归覆盖“窄栏间空白 + 稀疏跨越元素 + 跨栏页脚”，避免回到整页宽候选。

## 确定性

两篇论文都独立生成两次复核包，并逐文件计算 SHA-256：

| 样本 | 每次文件数 | 不一致文件 |
|---|---:|---:|
| Topological | 21 | 0 |
| Pan-cancer | 97 | 0 |

比较范围包含 `review-index.json`、每页 `layout-task.json`、原页预览、
候选叠加图和复核说明。

## 已知限制

- 规则阶段故意允许过度合并或过度拆分，最终边界、类型、阅读顺序和
  caption 绑定仍由视觉 AI 复核。
- 混合 Figure/Caption、标题页和方法/参考文献密集页通常需要 AI split/merge。
- 本轮没有把 AI 复核结果冒充人工真值，也没有据此训练分类器。
- 训练前仍需要积累按整篇论文划分、经过复核的 `final-layout.json`。

## 临时证据目录

证据位于系统临时目录中的以下唯一目录，未写入稳定仓库或 Obsidian：

- `paper2md-layout-validation-topological-v4`
- `paper2md-layout-validation-topological-v5`
- `paper2md-layout-validation-pan-cancer-v2`
- `paper2md-layout-validation-pan-cancer-v3`

这些目录包含原 PDF 的页面预览，不能提交到 Git。
