# Final ArticleTree v0.2

> 状态：已实现（2026-08-21）

## 为什么需要第二棵树

E5 的 `paperwright-article-tree-v0.1` 是 prepare 阶段的源元素守恒树。它证明每个
PhysicalDocument 元素只出现一次，并记录 Recipe 的 keep/render/exclude 决策，但不包含
最终段落、视觉槽位和 caption 关系，因此不能单独生成 ArticleModel。

`paperwright-article-tree-v0.2` 是 apply 阶段的最终文章树。主链现在是：

```text
PhysicalDocument
  → source-element tree v0.1 + PaperRecipe
  → reviewed/refined layouts
  → final ArticleTree v0.2
  → ArticleModel v0.1
  → article.md + reader.json
```

标准和完整证据包把 v0.1 保存为
`_paperwright/02-structure/source-element-tree.json`；所有证据级别都保存最终树为
`_paperwright/article-tree.json`。prepare 目录继续使用原文件名 `article-tree.json`，以
兼容已经存在的复核工作区。

## 契约

最终树绑定：

- source PDF SHA-256；
- PhysicalDocument 确定性 SHA-256；
- 上游 source-element tree 或 reviewed layouts 的 SHA-256；
- 连续有序的 article block 节点；
- source spans、视觉 assets 和 `places/caption-of` relations；
- `generated_text_count = 0`。

block 节点保存最终单行 Markdown，因为 ArticleModel 不再允许从 writer 的私有中间列表
直接构造。这里的 Markdown 是已验证的 source projection，不给 Recipe 或模型增加正文
生成权限。验证器先检查树形、输入身份、顺序和汇总，再投影并完整验证 ArticleModel。

## 文本复核链

`text-package` 不再直接把修改后的 ArticleModel 作为规范结果。它先应用受限 TextReview，
将 task 和 review 的规范哈希绑定到一棵新的 final ArticleTree，再从树投影 ArticleModel、
Markdown 和 Reader。源 v0.9 包若来自旧版本、没有 final ArticleTree，仍可读取；这种兼容
路径的 `physical_document_sha256` 明确为 `null`，新生成的布局包不会出现该状态。

## 兼容边界

- v0.1 与 v0.2 不是同一阶段的互斥版本：v0.1 审计物理元素，v0.2 规范最终文章。
- ArticleModel v0.1、Reader v0.1 和 manifest v0.9–v0.11 未改变。
- `ReaderCompilation.article_model()` 仍保留为 Python 兼容入口，但内部也必须先构造
  final ArticleTree，不能绕过它。
- direct 兼容 writer 不在本次迁移范围；Hybrid layout/text-package 两条生产路径已经
  汇合到 final ArticleTree。

## 尚未解决

- v0.2 目前表达既有 block/asset/relation 集，还未开放 section hierarchy、split/merge
  或正文引用关系。
- 树保证确定性来源链和无旁路投影，不等同于证明上游布局语义一定正确。
- 下一轮仍需用新的冻结论文测量 Recipe 的首页家具、Table 图片回退和原生 Figure 补全。
