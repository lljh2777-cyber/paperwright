# Paper2MD Phase 6：源码 Alpha 发布候选审查报告

## 结论

`PASS_WITH_LIMITATIONS`

当前候选适合作为项目所有者控制的源码 Alpha 供审阅、本地安装和有限试用，
并建议在本地复测后合并开发分支到 `main`。它不具备正式公开发行、PyPI、
容器或 PDFium 二进制分发批准。

## 基线与变更

- 仓库：`lljh2777-cyber/Paper2MD`
- 分支：`agent/v2-rebuild`
- 基线：`47e31abb58d062e1da0ecf92a2a303afddaa39af`
- 产品版本：`0.6.0a0`
- Phase 6 产品算法修改：0
- 局部修复：README/复现/Windows 状态更新、RC 变更说明、支持矩阵、
  六项文档/版本/schema/许可证一致性测试。

Phase 5 `windows_validation*` 和全部历史机器证据未覆盖。

## 产品与 CLI 审查

- `paper2md --version/--help/convert/batch/validate-model` 与入口一致；
- 默认 backend 为 PDFium；
- region-render 默认 off，auto 仅显式 opt-in；
- PDFBox 未绑定时明确失败；
- batch 非递归、确定性排序、逐文档隔离；
- 路径冲突、workspace 越界、symlink 和既有输出均有安全拒绝；
- 单文档和 batch 使用原子输出，不覆盖旧结果；
- manifest v0.4/v0.5、PhysicalDocument v0.2 与 batch schema 保持兼容；
- 错误类别覆盖 corrupt/unsupported/backend unavailable/output conflict/
  path safety/configuration/internal。

## 实测

- 单元测试：原有 94 + 新增 6 = 100/100；
- Stage B 内容断言：13/13；
- Stage C：12/12；
- Phase 3：17/17；
- Phase 4 spike：15/15；
- Phase 4 auto：11/11；
- Phase 5 汇总：8/8；
- Phase 6 batch：8/8；
- 临时 wheel/sdist 安装后命令：12/12；
- failure 0，skip 0；
- compileall、repo policy、secret scan 和 diff check 通过。

Linux 是本轮实测平台。Windows 只引用 Phase 5 已提交的独立实测证据，不
冒充本轮再次验证；macOS 未验证。

## 许可证分层

1. **仓库合并/所有者控制的本地试用**：可继续，有限制；
2. **source-only 审查交付**：可供项目所有者和指定审查者内部使用；
3. **公开源码包再分发**：未批准，项目级许可证仍为 `NOASSERTION`；
4. **wheel/sdist/PDFium 二进制分发**：未批准，agg23 和 bundled notices
   仍待发布级审查。

没有发现已确认的实际许可证冲突，但“没有发现冲突”不等于获得分发许可。

## 范围限制

不调用 LLM、生成式 API 或云 OCR。不支持 OCR/扫描 PDF、语义表格、公式
LaTeX、完整 PDFBox、GUI、服务器/API、容器、公开 PyPI、tag、签名或正式
release。复杂版式可诚实 degraded。

## 下一步

本地 Windows 应审计 source-only ZIP、应用 patch、运行全量测试和安装
验证。通过后可合并到 `main`，但仍需保持 Alpha 标识并锁定正式分发。
