# HybridPipeline 与 run contract v0.2

`paperwright-hybrid-run-v0.2` 是 PaperWright 唯一 Hybrid 产品入口的可恢复运行契约。
它把模型桥和确定性投影包在核心状态机内，但不把 token、价格或预算作为产品路由条件。

## 1. 用户入口

```bash
paperwright hybrid input.pdf output-dir
```

没有确认过的 Content ROI 时，首次运行会生成
`output-dir.paperwright-run/layout-proposal/content-roi.json` 并停在
`confirm_content_roi`。确认每页边界、填写 `review_status=confirmed` 与 `reviewer` 后：

```bash
paperwright hybrid input.pdf output-dir \
  --resume --content-roi-json confirmed-roi.json
```

输入 PDF、输出路径、后端、提取配置、证据等级与参考文献策略都绑定在 `run.json` 中；
恢复时发生漂移会被拒绝。

## 2. 五个检查点

| 阶段 | 职责 | 核心完成条件 |
|---|---|---|
| `evidence` | 提取、确认 ROI、候选与 issue routing | review index 和路由文件通过哈希绑定 |
| `layout` | L0 布局、局部 L2、跨页 caption 关系 | 每页 `final-layout.json` 已校验并逐文件绑定 |
| `projection` | `layout-apply`、ArticleModel、Completeness、精确文本 issue | 基础输出包可完整复核，文本任务与 `resolve-issues.json` 已绑定 |
| `text` | 精确 L1，失败时 L3，生成可选派生包 | 基础包或文本派生包可完整复核 |
| `verification` | 选择最终活动包并复核 manifest 哈希链 | 最终 manifest 和 completeness 报告已绑定 |

阶段状态为 `pending/running/waiting/completed/failed`。失败记录准确的阶段、异常类型和
诊断；恢复只重新进入失败阶段，已经完成的阶段不会再次调用 resolver。

## 3. 恢复规则

```bash
paperwright validate-hybrid-run output-dir.paperwright-run/run.json
paperwright hybrid input.pdf output-dir --resume \
  --content-roi-json confirmed-roi.json
```

恢复前，核心会重新计算所有已登记产物的 SHA-256。任一前序产物缺失或变化都会立即
阻断恢复。默认过渡适配器还具有两项幂等行为：

- `projection` 发现已有基础输出目录时，先完整验证其 manifest 和文件哈希；验证通过才
  复用，不会因目录存在直接认定成功；
- `text` 发现已有文本派生包时同样先完整验证；已有 L1/L3 review 会先按 TextTask 校验
  后再尝试继续打包。

检查点保证“从最近一个完成阶段继续”，不承诺在一次模型请求的内部断点续传。模型桥
若留下不完整且拒绝覆盖的单文件，需要人工审查该文件或从新运行目录重启。

## 4. Resolver 边界

Python resolver 仍是可替换 provider，但 v0.2 每次只接收一个阶段：

```python
def resolver(request: HybridResolverRequest) -> None:
    assert request.stage in {"layout", "projection", "text"}
```

`request.run_dir` 提供运行级产物位置；默认适配器把精确的投影后 issue plan 写入
`run_dir/resolve-issues.json`。核心在每次 resolver 返回后自行验证阶段产物，因此 provider
不能直接把某阶段标记为完成。

`--defer-resolution` 会在当前 resolver 阶段进入 `provide_resolver`，供 Python API 注入
自定义实现。

## 5. v0.1 兼容边界

v0.2 改变了阶段数组和 resolver 请求，旧 `paperwright-hybrid-run-v0.1` 不可直接恢复。
项目仍处于 alpha：旧运行可保留作证据，但需要以新运行目录重新开始，不能伪造版本字段
迁移。旧的一次性 `tools/run_routing_plan.py` 调用仍可用，其默认 `--stage all` 保持原行为。

## 6. 离线验证

两页 born-digital fixture 覆盖：

- ROI 暂停与恢复；
- L0 五阶段完整运行；
- 在 `projection` 注入失败后，恢复只调用 `projection` 和 `text`，不重复 `layout`；
- 完成阶段的逐产物哈希复核；
- `run.json` 不包含 token 或费用字段。
