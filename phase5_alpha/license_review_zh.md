# Paper2MD Phase 5 Alpha 许可证与分发边界审查

本文件是工程供应链记录，不是法律意见。

## 结论

当前没有发现会阻断 Paper2MD **源码研发和 Work/用户本地安装**的实际许可
冲突，因此 Alpha 源码工作可以继续。但正式 wheel/PDFium 二进制再分发、
安装器、容器和公开 PyPI 发布均未获批准。

主要原因：

1. Paper2MD 项目自身尚未在 `pyproject.toml` 声明最终许可证；
2. `pypdfium2==5.3.0` 自带 PDFium 平台二进制及多项第三方 notices，正式
   分发需要平台级 SBOM/NOTICE 复核；
3. `agg23` 随包提供了一份非标准的宽松授权文字，但当前没有在本项目中
   擅自赋予标准 SPDX 标识，继续记录为
   `LicenseRef-agg23-permissive-text / SPDX NOASSERTION`；
4. Alpha source-only ZIP 不含 wheel、sdist、PDFium、JAR 或任何依赖
   二进制，只保留准确版本约束和哈希化证据。

## 直接运行依赖

| 组件 | 锁定版本 | 许可证证据 | Alpha 本地安装 | 正式二进制分发 |
|---|---:|---|---|---|
| pypdfium2 | 5.3.0 | 包元数据、Apache-2.0/BSD-3-Clause 文件 | 可继续 | 待 bundled notices 审查 |
| PDFium | 145.0.7616.0 | PDFium 与 binaries license 文件 | 可继续 | 待平台 SBOM/NOTICE |
| Pillow | 12.2.0 | 元数据 `MIT-CMU` 与 LICENSE | 可继续 | 需保留 notice |
| agg23 | 2.3 | 非标准 permissive notice | 不阻断 | `NOASSERTION`，待法律复核 |

PDFBox 仍只是显式不可用的接口；本阶段没有下载、捆绑或执行 JAR，不能把
接口存在描述为 PDFBox 后端已经交付。

## 构建依赖

`setuptools>=68` 只用于源码构建，测试环境实际为 82.0.1；wheel 工具为
0.47.0。临时构建出的 wheel/sdist 只用于隔离安装验证，未进入 Git 或
source-only 交付。

## Alpha 与正式发布的区别

本阶段允许：

- 提交 Paper2MD 源码、schema、测试和文档；
- 用户在自己的 Python 3.10–3.13 环境按版本约束安装依赖；
- 本地处理用户自行提供的 born-digital PDF。

本阶段不批准：

- 将 PDFium/pypdfium2/Pillow 二进制复制进项目包、安装器或容器；
- 公开 PyPI 发布、签名 release 或商业分发声明；
- 声称已解决所有传递依赖的 NOTICE、出口或平台差异问题；
- 声称 Paper2MD 项目本身已经有最终开源许可证。

机器可读逐组件记录及证据哈希见
`phase5_alpha/license_inventory.json`。
