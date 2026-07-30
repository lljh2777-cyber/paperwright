# Paper2MD Alpha RC 合并 main 建议

## 结论

建议：**在项目所有者审阅 Phase 6 source-only patch 后，将
`agent/v2-rebuild` 合并到 `main`，但继续标记为源码 Alpha，且不得同时
创建公开 release、tag、PyPI 包或二进制分发物。**

这是一项 `MERGE_WITH_CONDITIONS` 建议，不是正式发布批准。

## 支持合并的证据

- 权威基线 `47e31abb58d062e1da0ecf92a2a303afddaa39af` 可公开读取；
- 原有 94 项测试和新增 6 项 RC 一致性测试全部通过；
- batch 8/8，包含部分失败隔离、确定排序、输出冲突和安全拒绝；
- 临时 wheel/sdist 各自安装后完成 12/12 命令检查及内容确定性比较；
- 默认 PDFium、region-render off、auto opt-in、PDFBox 明确失败均与文档一致；
- Phase 5 Windows 94/94、8/8、12/12 证据保持原样；
- 没有修改解析、Figure、caption、阅读顺序或 region-render 算法。

## 合并条件

1. 本地 Windows 从本交付 patch 做 fresh-tree 复测；
2. 确认 source-only ZIP 和候选 Git diff 不含 PDF、图片输出、wheel/sdist、
   PDFium/JAR/binary、缓存或凭据；
3. 合并说明明确 `0.6.0a0`、Alpha、非正式 release；
4. 在邀请公众复用或再分发前，由项目所有者选择并提交项目级许可证；
5. 在任何 wheel、安装器、容器或 PDFium 二进制分发前完成
   agg23/bundled notices/SBOM 审查。

## 不应随合并执行

不要创建 tag、GitHub release、公开 PyPI、容器、签名或二进制附件；不要
把 `NOASSERTION` 描述为许可通过。
