# Paper2MD v2-bootstrap 测试报告

## 结论

Stage A bootstrap 的当前正式测试为 **27/27 通过，0 failure，0 skip**。
此外，fixture 检查、CLI 版本、CLI 模型验证、Python 编译、diff whitespace
和仓库存储政策检查均通过，共 7 个顶层检查 7/7 通过。

## 实际环境

- CPython 3.12.13；
- Linux 6.12.13 x86_64；
- bootstrap 运行时第三方依赖：0；
- PDFium/PDFBox：未下载、未加载、未执行。

精确版本、UTC、argv、stdout/stderr 路径、大小、SHA-256 和 exit code 位于
`stage_a/test_summary.json`。

## 测试覆盖

| 类别 | 直接断言 |
|---|---|
| PhysicalDocument | NFC Unicode、bbox 正面积、有限数、页内边界、连续页码、唯一元素 ID、provenance 必填 |
| 确定性 | PhysicalDocument 与 manifest 的 canonical JSON 逐字节一致、稳定 SHA-256 |
| Schema | 两个 schema 均为 Draft 2020-12、封闭顶层字段、有效实例通过、非法 manifest 被拒绝 |
| CLI | 有效模型输出结构化验证结果；非法模型非零；后端缺失明确返回 4 |
| 路径安全 | 缺失输入、已有输出、workspace 越界、输入/输出包含冲突均拒绝 |
| 后端边界 | PDFium/PDFBox stub 不伪造输出；重复注册后端拒绝 |
| 仓库政策 | PDF/图片/binary/JAR/秘密模式/超过 5 MiB/非白名单扩展名检查 |

## 历史失败

首次完整运行得到 27 项中 25 通过、2 失败：

1. `test_api_uses_injected_backend` 的测试预期错误地把注册别名当作引擎身份；
2. `test_input_inside_output_is_rejected` 只是不匹配实际且正确的拒绝分类文案。

两项均为测试/分类精度问题，不是绕过安全门禁。完成最小修正后重新运行
全部 27 项，通过 27/27。首次运行发生在正式 runner 建立前，精确 UTC
未留存，已在机器摘要中明确标为 `not_recorded_before_runner_was_added`，
没有伪造时间。

## 质量边界

- `paper2md convert` 尚不能解析 PDF，这是 bootstrap 的明确非目标；
- 后端不可用会非零失败，不会生成假 `article.md`；
- 当前 schema 的跨字段约束由 Python semantic validator 补充，不能把 JSON
  Schema 文件可解析等同于完整语义通过；
- 尚未进行真实论文、性能、RSS、跨平台或正式分发验证；
- `agg23=NOASSERTION` 继续锁定正式分发批准。
