# Paper2MD Phase 5：Alpha 集成与可用性验证报告

## 阶段结论

自检结论：`PHASE5_ALPHA_PASS_WITH_LIMITATIONS`。

Paper2MD 已具备标准源码安装入口、单文档转换、保守批处理、严格配置、
机器可读错误分类及可复现的 Linux 安装验证。Phase 4 算法和历史证据未被
修改；8 篇 RW2 的默认关闭兼容结论直接复用权威摘要，没有联网或重建大型
输出。

这仍是源码 Alpha，不是公开 release、二进制分发批准或 Windows 已验证版本。

## 权威基线与范围

- Git 分支：`agent/v2-rebuild`
- 基线：`5656eeff3d95ed7a3f025c5763bd94c5be565abe`
- 产品版本：`0.6.0a0`
- Python：`>=3.10,<3.14`
- 运行依赖：`pypdfium2==5.3.0`、`Pillow==12.2.0`
- 默认后端：PDFium
- region render 默认：`off`
- PDFBox：接口保留、未绑定，明确失败

`phase5_alpha/baseline_authority.json` 固定了 Phase 4 摘要、报告、
`region_render.py` 和 manifest schema 的基线哈希；阶段末机器检查均保持
一致。

## 新增 Alpha 能力

### 标准安装与入口

`pyproject.toml` 声明 console script：

```text
paper2md = paper2md.cli:main
```

安装后实际验证：

- `paper2md --version`
- `paper2md --help`
- `paper2md convert`
- `paper2md batch`
- `paper2md validate-model`

### Batch

`batch` 支持：

- 非递归 `--input-dir`；
- 重复 `--input-file`；
- UTF-8 `--file-list`；
- 确定性排序和 `0001-name` 输出；
- 默认遇错停止或 `--continue-on-error`；
- 每文档独立原子输出；
- 默认 region render off，可显式 auto 与候选上限；
- 现有输出、路径嵌套及 symlink 安全拒绝。

`batch_summary.json` 记录输入名称/hash/字节、相对输出目录、backend、warnings、
degraded、错误类别和状态。绝对输入路径不写入摘要。UTC/耗时隔离在
`runtime`，不参与 `deterministic_content_sha256`。

错误类别覆盖：

```text
corrupt / unsupported / backend_unavailable / output_conflict
path_safety / configuration / internal
```

## 验证

### 单元与历史回归

- 原有：77/77
- Phase 5 新增：17/17
- 总计：94/94
- failure：0
- skip：0

新增测试覆盖安装元数据、help 命令、配置优先级、严格配置、batch
成功/部分失败/停止、确定性排序、summary 契约、两轮 hash、输出冲突、
输入输出嵌套、symlink、file list、默认 off、auto opt-in、PDFBox 明确失败、
错误分类和损坏 PDF 原子清理。

### Batch 独立机器场景

8/8 场景通过：

- 两轮成功转换及逐文件 hash；
- continue-on-error；
- stop-on-error；
- auto opt-in；
- PDFBox unavailable；
- 已有输出拒绝且 marker 未变；
- 扫描目录内嵌套输出拒绝。

### 构建与安装

最终临时构件：

| 构件 | 字节 | SHA-256 | 内容审计 |
|---|---:|---|---|
| wheel | 51,257 | `916d719017f4a3c83b69ec46d0588323c01c6b20ae6937c1e30090daae12d60e` | 25 成员，3 schema 齐全，0 禁带 |
| sdist | 59,016 | `3fcefb3a95bc55dc0f8b1007835f0d80953240ae2a8f2217a0fe7f56a5591364` | 41 常规文件，0 禁带/危险成员 |

wheel 与 sdist 分别安装到新 venv，每个完成 install 加 5 个 console command，
合计 12/12；两者 batch 输出树及规范化 summary hash 相同。

首次 `install-v1` 的 sdist pip 构建因默认全局 pip cache 只读而失败。该失败
保留在外部运行现场和 `test_summary.json`；修复为阶段隔离
`PIP_CACHE_DIR` 后，install-v2 与最终 install-v3 均通过。

## 安全与数据边界

- 单文档及 batch 文档输出均经临时目录原子提交；
- 损坏文档不留下半成品；
- batch 不递归，不跟随输入 symlink；
- 输出根目录已存在时拒绝，不覆盖 marker 或旧结果；
- summary 不写绝对输入路径、cookie、令牌或环境变量；
- source-only 交付不含 PDF、真实图片/输出、wheel/sdist、PDFium/JAR、
  binary、cache、venv 或凭据。

## 许可证

没有发现阻断源码 Alpha/本地安装的实际许可证冲突。但：

- Paper2MD 自身许可证仍 `NOASSERTION`；
- PDFium bundled notices 仍需发布级平台 SBOM/NOTICE；
- `agg23` 保留 `LicenseRef-agg23-permissive-text / SPDX NOASSERTION`；
- 正式二进制再分发、PyPI、安装器和容器均未批准。

详见 `license_inventory.json` 与 `license_review_zh.md`。

## 明确限制

- 云端只验证 Linux；Windows 等待本地独立复测；
- 安装 venv 通过 system-site-packages 读取 Work 已锁定依赖，不是自包含
  离线安装器；
- OCR、扫描 PDF、语义表格、公式 LaTeX、纯矢量无 native group、
  PDFBox 完整实现均不在 Alpha；
- 不提供 GUI、Web API、容器、签名、公开 PyPI 或正式 release；
- `limits.timeout_seconds` 尚不是跨平台硬 sandbox；
- 真实论文泛化仍限于已有 8 篇小样本证据。

## 下一门禁

本地应从 patch 在全新 `5656eeff` 工作树复测 Windows 安装、94 项测试、
console entry、batch、安全行为和 source-only 内容。通过并提交远端 SHA
前，本阶段不进入 Phase 6。
