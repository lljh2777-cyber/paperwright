# Phase 6 Alpha RC Windows 本地验证补充

结论：`PASS_WITH_LIMITATIONS`。

本次在 Windows、Python 3.11.2、pypdfium2 5.3.0、PDFium
145.0.7616.0、Pillow 12.2.0 和 wheel 0.47.0 环境中独立复核 Phase 6
Alpha RC。

## 最终结果

- 单元测试：100/100 通过，无失败或跳过。
- 批处理检查：8/8 通过，内容、安全和双轮确定性断言通过。
- wheel 与 sdist 均完成隔离安装，12/12 个安装后命令通过。
- Phase 6 汇总检查：8/8 通过。
- 历史机器摘要继续通过，产品解析及 region-render 算法未修改。

## Windows 兼容修复

Phase 6 的受保护证据检查最初直接比较原始字节。仓库设置
`core.autocrlf=true` 后，Windows 会把 LF 检出为 CRLF，造成哈希误报。
校验器现在先把 CRLF 规范化为 LF，再严格比较规范化字节数和 SHA-256。
受保护文件清单、期望大小和期望哈希均未放宽。

运行证据位于仓库外：
`%LOCALAPPDATA%\Temp\paper2md-phase6-windows-runtime`。

## 许可证与范围

- 建议在项目所有者控制范围内把开发分支合并到 `main`，继续标记为源码
  Alpha。
- 项目许可证仍为 `NOASSERTION`；公开 source-only 包、wheel、sdist 和
  PDFium 二进制分发仍未获批准。
- 本次没有创建 tag、GitHub Release、PyPI 包或二进制附件。
