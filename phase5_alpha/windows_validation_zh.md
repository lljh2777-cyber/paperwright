# Phase 5 Alpha Windows 本地验证补充

结论：`PASS_WITH_LIMITATIONS`。

本次在 Windows、Python 3.11.2、pypdfium2 5.3.0、PDFium
145.0.7616.0、Pillow 12.2.0 和 wheel 0.47.0 环境中复核了云端
Phase 5 Alpha 交付。

## 最终结果

- 单元测试：94/94 通过，无失败或跳过。
- 批处理检查：8/8 通过，内容断言和双轮确定性通过。
- 安装检查：wheel 与 sdist 均完成隔离安装；12/12 个安装后命令通过。
- Phase 5 汇总检查：8/8 通过。
- Phase 4 的 region-render 算法、既有证据和 manifest schema 未被修改。

## Windows 兼容修复

首次复核暴露了三个环境问题：

1. 测试代码使用系统默认编码读取 UTF-8 JSON，在中文 Windows 上被
   GBK 解码，导致 3 项测试报错。修复为显式指定 UTF-8。
2. 两个 Phase 5 验证工具使用系统默认编码解码子进程输出，修复为
   显式指定 UTF-8。
3. `core.autocrlf=true` 会把受保护文本的 LF 检出为 CRLF。基线校验
   现在先把 CRLF 规范化为 LF，再严格比较规范化字节数和 SHA-256；
   文件清单及期望哈希没有放宽。

初始失败现场保留在仓库外的
`%LOCALAPPDATA%\Temp\paper2md-phase5-windows-runtime`，不进入 Git。

## 限制

- 项目级许可证仍为 `NOASSERTION`，正式二进制分发尚未获准。
- 本次结论仅支持源码 Alpha，不代表 PyPI、容器或正式发布审查通过。
- 构建工具在 Windows 上产生过非致命日志输出；实际构建、安装、
  命令结果和内容断言均通过。
