# Paper2MD 文本复核协议 v0.1

## 目标与边界

混合布局阶段由人工或视觉模型只决定页面几何和语义区域；Paper2MD 从原生 PDF
文字层恢复正文并生成规范 `article-model.json`。文本复核阶段再把该模型投影成
不含页面图像、source span、资产与关系的 `text-task.json`，供纯文本模型做保守整理。

Paper2MD 核心不会自行调用模型、外部 API 或云服务。主 Agent 负责选择具体模型、
确认隐私范围、传递任务、接收 JSON，并调用本地验证器。

## 命令

```bash
paper2md text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON
paper2md validate-text-task TEXT_TASK_JSON --article-model ARTICLE_MODEL_JSON
paper2md validate-text-review TEXT_REVIEW_JSON --task TEXT_TASK_JSON
paper2md text-apply ARTICLE_MODEL_JSON TEXT_TASK_JSON TEXT_REVIEW_JSON REVIEWED_MODEL_JSON
paper2md text-package SOURCE_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_PACKAGE
paper2md validate-text-package OUTPUT_PACKAGE
```

所有输出文件都拒绝覆盖。`text-apply` 只生成新的 Article Model；`text-package`
则保留源包不变，原子写出完整的 manifest v0.10 派生包，重新投影 `article.md`、
`reader.json` 并加入 task、review 与验证报告。首版只接受完整的 manifest v0.9
源包，避免脱离已验证的视觉布局来源。

## 允许的操作

每个 block 最多出现一次 `replace-markdown`：

- `format-only`：规范化可见文本必须与原文完全相同，可用于 Markdown 强调与空白整理；
- `dehyphenation`：只能删除词内断行形成的连字符及其后空白，例如
  `multi- modal` → `multimodal`。

视觉槽位不可编辑。稳定 ID、block kind、order、source span、asset ID、资产与关系
全部不可变。v0.1 不允许拼写/标点/事实改写，不允许拆分、合并、删除或重排 block，
不允许改变 Markdown 标题层级，也不允许依据模型知识补写正文、图注、公式或引用。

## 哈希链

Text Task 记录源 PDF SHA-256、Article Model 契约和规范 JSON SHA-256；每个 block
另记录原 Markdown 与规范化可见文本哈希。Text Review 必须回传 task、source、model
哈希，并为每次替换回传目标 block 的原 Markdown 哈希。任何过期或串线任务都会
明确失败，不会尝试模糊匹配。

派生包的 manifest v0.10 还记录父 manifest、源 Article Model、task、review 和
JSON 验证报告的 SHA-256。输出清单逐文件绑定全部交付文件，因此修改正文、Reader、
图片、复核记录或报告中的任意一个文件都会使完整包校验失败。

## 多 Agent 分工

- 视觉子 Agent：只接收页面图、ROI、布局任务与几何说明，只返回 final layout JSON；
- 文本子 Agent：只接收 text task JSON，只返回 text review JSON；
- 主 Agent：保管原 PDF 与哈希链，运行所有验证命令，决定是否接受并交付新模型。

仓库内 [`paper2md-agent-workflow`](../skills/paper2md-agent-workflow/SKILL.md)
skill 固化了这一协调流程。
