# HybridPipeline 与 run contract v0.1

> 历史契约，当前实现已升级到 [run contract v0.2](HYBRID_RUN_V0.2.md)。v0.1 的
> 三阶段运行不能直接冒充 v0.2 恢复。

`paperwright-hybrid-run-v0.1` 是 PaperWright 唯一 Hybrid 产品入口的运行契约。它解决
“准备、桥脚本、应用和文本返修各自启动后，无法判断一篇论文跑到哪里”的问题；它不是
模型费用或 token 预算系统。

## 1. 用户入口

```bash
paperwright hybrid input.pdf output-dir
```

若未提供确认过的 Content ROI，命令生成
`output-dir.paperwright-run/layout-proposal/`，把 `run.json` 标记为
`awaiting_input`，然后安全返回。复制提案、确认每页边界并填写 `review_status=confirmed`
与 `reviewer` 后继续：

```bash
cp output-dir.paperwright-run/layout-proposal/content-roi.json confirmed-roi.json
# 审核并编辑 confirmed-roi.json
paperwright hybrid input.pdf output-dir \
  --resume --content-roi-json confirmed-roi.json
```

首次运行已有确认 ROI 时，可直接一条命令完成：

```bash
paperwright hybrid input.pdf output-dir --content-roi-json confirmed-roi.json
```

非默认的 `--run-dir`、提取配置、证据等级或参考文献策略在恢复命令中必须保持一致；
`run.json` 会拒绝输入哈希、输出路径或配置漂移。

## 2. 三个检查点

| 阶段 | 内容 | 完成条件 |
|---|---|---|
| `evidence` | 提取、ROI、候选关系任务、issue routing | review index 与路由产物已哈希绑定 |
| `resolution` | L0 + 局部 L2、布局投影、精确 L1/L3 | resolver 成功生成候选最终包 |
| `verification` | manifest、完整文件清单、文本派生包重放校验 | 最终 manifest 及 completeness 已绑定 |

阶段状态为 `pending/running/waiting/completed/failed`；运行状态为
`running/awaiting_input/completed/failed`。失败会保留异常类型、阶段与诊断，不把半成品
宣称为完成。

## 3. 产物和权限

- `run.json` 绑定源 PDF SHA-256、路径、配置、阶段尝试次数和关键产物 SHA-256；
- 已确认 ROI 会复制为运行目录中的 `confirmed-content-roi.json`，避免外部文件后来漂移；
- `issue-routing.json` 是主路由语义，`routing.json` 只作为旧执行器适配；
- L2 主协议是 candidate relations；现有 `visual-direct` FinalLayout task 只是校验兼容层；
- `--defer-resolution` 可让核心停在 `provide_resolver`，由 Python API 注入其他 provider；
- run contract 不含 token、价格、费用估算或预算上限。模型调用原始观测属于 bridge/evaluation。

## 4. 校验与恢复边界

```bash
paperwright validate-hybrid-run output-dir.paperwright-run/run.json
```

恢复前会复核此前记录的关键产物哈希。v0.1 支持 ROI 等显式输入检查点和在尚未生成
输出目录时重试 resolver；若 resolver 失败后已经留下部分输出目录，会拒绝把它当作可
恢复完成状态，要求换新输出路径重新运行。后续版本会把当前过渡执行器内部的布局、
投影和文本阶段拆成更细的原子检查点。

## 5. v0.1 校准

- 自生成两页论文 fixture：从 ROI 暂停点恢复，L0 路径生成并复核 manifest v0.9 包；
- *Attention Is All You Need*：15 页 evidence 阶段、15 个局部 L2 issue、160 个关系候选，
  `run.json` 正确进入 `confirm_content_roi`；
- 校准只检查契约、路由和候选完整性，没有调用外部模型，因此不代表 L2 语义质量已验收。
