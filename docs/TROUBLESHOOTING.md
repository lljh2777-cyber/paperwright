# Alpha 故障排查与限制

## 错误分类

| category | 含义 | 常见处理 |
|---|---|---|
| `corrupt` | PDFium 无法打开或 PDF 损坏 | 换用合法完整的 born-digital PDF |
| `unsupported` | 输入类型不支持 | 确认扩展名和 Alpha 范围 |
| `backend_unavailable` | 后端未绑定 | 安装锁定 PDFium；PDFBox 当前必然失败 |
| `output_conflict` | 输出已存在或与输入嵌套 | 使用全新独立输出目录 |
| `path_safety` | 路径或 symlink 越界 | 使用常规文件及明确 workspace |
| `configuration` | JSON/CLI 配置无效 | 对照配置参考 |
| `internal` | 未分类产品错误 | 保留 summary 和 stderr 后报告 |

## 常见问题

### `pip install .` 提示找不到 `pyproject.toml`

当前终端不在项目根目录。先进入同时包含 `pyproject.toml`、`src/` 和
`README.md` 的目录，再执行安装。

### 找不到 `python` 或 `py`

安装 64 位 Python 3.10、3.11 或 3.12。Windows 官方安装器需要启用
Python Launcher；Linux 可能还需要发行版提供的 `python3-venv` 和
`python3-pip`。

### PowerShell 不允许激活 `.venv`

只为当前 PowerShell 窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

不希望改变执行策略时，直接运行：

```powershell
.\.venv\Scripts\python.exe -m paper2md --help
```

### 安装后找不到 `paper2md`

通常是虚拟环境尚未激活，或者关闭终端后没有重新激活。也可以始终使用：

```bash
python -m paper2md --help
```

### 依赖下载失败

首次安装需要访问 Python Package Index。检查网络、代理和系统时间；不要从
来源不明的网站下载 PDFium 二进制。离线安装包尚未提供。

### 提示 Python 版本不兼容

当前声明范围是 Python 3.10–3.12。Python 3.13、macOS 和 ARM 平台尚未完成
项目验证。

### PDFBox 为什么失败？

Alpha 只保留 PDFBox 接口，没有捆绑或执行 JAR。选择
`--backend pdfbox` 会返回明确的 `backend_unavailable`，不会伪造成功。

### 扫描 PDF 为什么没有正文？

Alpha 不做 OCR。扫描件应被视为 unsupported/degraded 输入，而不是文字恢复
成功。

### 为什么表格不是 Markdown 表？

当前只在结构不可靠时保留文字并标记 `degraded`，不会猜测行列。

### 为什么有些纯矢量 Figure 没有 region asset？

auto 模式要求可追溯的 native Figure group；纯矢量且没有 native group 时
保守拒绝。Phase 4 的安全阈值本阶段没有修改。

### 为什么会看到不同 manifest 版本？

- region render `off`：manifest v0.4，保持旧默认输出兼容；
- `explicit/auto`：manifest v0.5，增加 `region_render_policy` 和区域证据。
- 旧混合布局结果：manifest v0.6，仍可读取；
- 当前 `layout-apply`：manifest v0.7，增加证据级别和自包含包清单。

这些是数据契约版本，不要求与 Python 包版本相同。当前包版本为 `0.7.0a0`。

详见 `docs/MANIFEST_MIGRATION_V0.5.md`。

## 明确不支持

- OCR/扫描 PDF 识别；
- 语义表格和公式 LaTeX；
- 深层 Figure/caption 语义；
- PDFBox 完整后端；
- GUI、Web 服务、容器或公开 PyPI release；
- macOS、Windows ARM、Linux ARM 和 Python 3.13。
