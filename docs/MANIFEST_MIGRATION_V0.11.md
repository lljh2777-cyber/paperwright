# manifest v0.11 L3 程序合成派生包迁移说明

manifest v0.11 只由 `text-package --synthesis-run` 写出，用于把 L3
程序合成（`tools/run_text_synthesize.py`）的生成代码、输入哈希与输出哈希
一并落盘并纳入完整哈希链。不带 `--synthesis-run` 的 `text-package` 仍写
manifest v0.10，行为不变。

## 新增文件

```text
_paperwright/06-text-review/synthesize-run.json
_paperwright/06-text-review/source-article-model.json
```

- `synthesize-run.json`：L3 溯源记录，版本
  `paperwright-synthesis-run-v0.1`。字段为脚本全文、执行器版本、源 PDF
  SHA-256、源 Article Model SHA-256、task SHA-256、review 规范 JSON
  SHA-256、reviewer 和 operation_count。**不含时间戳、绝对路径或凭据**。
- `source-article-model.json`：本次复核使用的源 Article Model 规范副本。
  text-task 故意不携带几何信息；保留该副本才能用相同 bbox 重新执行 DSL，
  证明同一输入重放得到同一输出。

两个文件都进入 manifest outputs 清单，逐文件绑定路径、大小与 SHA-256。

## manifest v0.11 新增顶层字段

```json
{
  "synthesis_run": {
    "contract_version": "paperwright-synthesis-run-v0.1",
    "executor_version": "paperwright-synthesize-v0.1",
    "path": "_paperwright/06-text-review/synthesize-run.json",
    "sha256": "<file sha256>",
    "task_path": "_paperwright/06-text-review/text-task.json",
    "task_sha256": "<same as text_review>",
    "review_path": "_paperwright/06-text-review/text-review.json",
    "review_sha256": "<same as text_review>",
    "source_article_model_path": "_paperwright/06-text-review/source-article-model.json",
    "source_article_model_sha256": "<same as text_review.source_article_model_sha256>"
  }
}
```

v0.10 包继续有效且不包含该字段；非 v0.11 manifest 出现 `synthesis_run`
会被拒绝。

## 校验行为

`validate-text-package` 对 v0.11 在 v0.10 全部校验之外额外执行：

1. 两个新增文件存在、内容为规范 JSON、哈希与 manifest 一致；
2. `source-article-model.json` 通过 Article Model v0.1 全量校验，且其
   哈希等于 task 绑定的源 Article Model SHA-256；
3. `synthesize-run.json` 的 task/review/source/model 哈希与实际文件一致；
4. **确定性重放**：用 run 中脚本 + task + 源 Article Model bbox 重新执行
   受限 DSL，产出的 operations 必须与 `text-review.json` 逐字段相同；
   任何分歧、过期输入或脚本篡改都明确失败。

validation-report 在 v0.11 增加 `synthesis_run_sha256` 字段与
`synthesis_run_replay` 检查项。

## 迁移命令

```bash
# L3 桥现在同时产出 review 与溯源记录
PYTHONPATH=src python tools/run_text_synthesize.py \
  article-model.json text-task.json text-review.json \
  --synthesis-run synthesize-run.json

paperwright text-package SOURCE_V09_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON \
  OUTPUT_V11_PACKAGE --synthesis-run synthesize-run.json
paperwright validate-text-package OUTPUT_V11_PACKAGE
```

## 兼容性

- 不带 `--synthesis-run`：仍写 manifest v0.10，不复制源 Article Model；
- 带 `--synthesis-run`：写 manifest v0.11，源 v0.9 父包保持不可变；
- 读取端继续接受 manifest v0.6–v0.10 的历史混合布局包；
- v0.11 不改变 Article Model v0.1、Reader v0.1、Text Task v0.2 或
  Text Review v0.2 的内部契约。
