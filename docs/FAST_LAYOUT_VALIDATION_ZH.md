# Fast 布局提取验证

本轮验证的结论是：`fast` 已从只读基准进入正式的布局准备流程，能稳定生成可复核的
v0.2 任务；`standard` 能按页识别复杂页面并只升级这些页面；布局应用可以复用带哈希
的提取缓存。它还不能替代视觉复核，也不能仅凭“运行成功”证明最终 Markdown 正确。

## 真实论文性能

| 样本 | 页数 | `layout-prepare fast` | 提取缓存 | 完整复核包 |
|---|---:|---:|---:|---:|
| Topological domains | 5 | 3.268 秒 | 1,031,381 bytes | 8,980,280 bytes |
| Pan-cancer spatial atlas | 24 | 13.311 秒 | 4,468,700 bytes | 46,513,264 bytes |

两篇均使用 TextPage 文字坐标、1.5 倍低分辨率页面图和栅格残余候选，任务契约均为
`paperwright-layout-task-v0.2`。Topological 在 Step 5 与 Step 6 两次独立准备中，全部
5 页的任务 SHA-256 逐页一致。

此前 Topological 的完整对象流程曾在 15 分钟后仍未完成，但当时没有保存可复现的
机器计时记录，而且旧流程包含的工作范围也不完全等同于本次 `layout-prepare`。因此该
观察只能说明原瓶颈显著，不能据此宣称精确加速倍数。

## 页面级风险升级

`standard` 对 Topological 选择第 2、4 页（零基索引 1、3）升级：第 2 页有 11 个
栅格区域和 100 个候选分隔关系；第 4 页有 94 个分隔关系。其余 3 页继续使用 fast。
Pan-cancer 的 24 页均未触发当前 v0.1 风险门槛。

这说明风险门禁能够抓到 Topological 第 2 页这种多面板图、图内文字和分隔框高度
密集的页面。但“未升级”只代表结构计数未越界，不等于页面语义必然正确。

## 视觉抽查

- Topological 第 2 页：正文 ROI 已排除期刊徽标、页码和版权行；左右 Figure 均有
  栅格候选覆盖。但叠加图含 100 个分隔关系，视觉噪声较高，必须升级或人工/AI 合并。
- Pan-cancer 第 2 页：左右正文、跨栏 Figure 和下方 caption 均形成可用候选。
- Pan-cancer 第 3 页：双栏正文、整幅工作流图和图注的上下关系清楚。
- Pan-cancer 第 5 页：整页 Figure 被单个视觉候选覆盖，页脚被正文 ROI 排除。

## 缓存与安全

复核包新增：

```text
extraction-cache/
├── physical-document.json
└── backend-warnings.json
```

`review-index.json` 保存两个文件的 SHA-256、PhysicalDocument 确定性哈希和警告数量。
`layout-apply` 同时验证原 PDF 哈希、缓存哈希和每页栅格掩膜哈希；缓存篡改测试会被
明确拒绝。没有缓存的旧复核包继续走原提取路径。

## 尚未完成的质量证明

本轮没有为新的 v0.2 任务伪造 `final-layout.json`，所以没有重新生成并评分两篇论文的
最终 Markdown。要完成最终质量对照，下一步应先对代表性页面确认布局，再检查正文
断词/重复、图内标签混入、标题完整性、图片链接、manifest 和对象使用完整性。

机器可读结果见 [fast_layout_validation.json](fast_layout_validation.json)。
