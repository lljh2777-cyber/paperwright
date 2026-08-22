# GROBID 人工 Gold 审计 v0.1

## 目的

该工具把 GROBID 独立评估的原始 JSON 转为可直接在本地浏览器填写的盲化审阅工作台。
它同时支持两项互相独立的工作：

1. 对每个 GROBID claim 标注角色与边界质量；
2. 从整篇 PDF 独立枚举 recall gold 单元，避免从“模型已经找到了什么”反推缺失项。

界面不会显示 PaperRecipe、ArticleTree 或下游是否采用某个 claim，也不会调用 AI 模型。

## 契约

- audit task：`paperwright-grobid-claim-audit-task-v0.2`；
- response：`paperwright-grobid-human-review-v0.1`；
- manifest：`paperwright-grobid-human-review-manifest-v0.1`。

v0.2 task 在原 v0.1 基础上补齐：

- 对应 PDFium 原生文字、原生 bbox 和 observation ID；
- 页面宽高；
- 成功论文的**全部页面**，包括没有任何 GROBID claim 的页面。

response 与 task 规范 JSON 的 SHA-256、document ID、PDF SHA-256 和 claim 顺序绑定。验证器
拒绝 claim 丢失、重复、乱序、未知标签、越界 gold bbox、`not_applicable` 下仍存在 gold
unit，以及伪造的 completion 计数。

## 使用界面

生成：

```bash
PYTHONPATH=src .venv/bin/python tools/prepare_grobid_human_review.py \
  /path/to/evaluation-run \
  /path/to/evaluation-run/human-review-gold-v0.1
```

输出目录存在即拒绝覆盖。每篇论文得到一个无第三方前端依赖的 HTML：

- 左侧按 claim type 或未标注状态过滤；
- 中央显示真实页面以及 GROBID 蓝框、PDFium 原生文字橙框；
- 右侧显示 GROBID/原生文字、alignment 分数、备注和五种固定标签；
- `1`–`5` 选择标签，左右方向键切换 claim；
- `Gold units` 模式可独立浏览全文每一页，枚举 title、abstract、section heading、
  Figure/Table caption 和 reference；
- 浏览器本地自动保存，支持导入和导出 task-bound JSON。

浏览器存储只用于恢复工作，**导出的 JSON 才是评估产物**。每次标注会话结束都应导出。

验证中间结果：

```bash
PYTHONPATH=src .venv/bin/python tools/validate_grobid_human_review.py \
  human-review-gold-v0.1/tasks/DOCUMENT.json \
  /path/to/DOCUMENT.human-review.json
```

最终提交增加 `--require-complete`；此时必须填写 reviewer、标完全部 claims，并将六类 recall
gold 状态全部设为 `complete` 或真实的 `not_applicable`。

## 本批产物

冻结运行的规范审阅入口为：

```text
paperwright-grobid-semantic-eval-v0.1/runs/baseline-ff8959f/
  human-review-gold-v0.1/index.html
```

它覆盖 7 篇成功论文、143 页和 1,944 个 claims。`manifest.json` SHA-256：
`7e075ded3f1105dd52364646dcba9062f4a8bbec0f5a78ff495632e06f877aa7`。

同级 `human-review-v0.1` 至 `human-review-v0.5` 是开发过程中拒绝覆盖所保留的浏览器 QA
迭代，不是规范 gold 入口，不应在其中开始正式标注。

## 完成边界

当前只完成了标注工具和空白 response 模板，尚无人工作出语义标签，因此仍保持
`semantic_accuracy_measured=false`。只有全部 response 通过 `--require-complete` 后，才能
汇总严格 precision/recall，并决定是否扩大某类 GROBID claim 的确定性权限。
