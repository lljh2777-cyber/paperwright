# 依赖与供应链边界

## Stage B 锁定运行时

| 构件 | 精确版本 | 当前用途 | 来源/许可证线索 |
|---|---:|---|---|
| Python | 3.10+ | 产品运行时 | PSF |
| pypdfium2 | 5.3.0 | PDFium Python 薄封装 | 官方 PyPI/GitHub；BSD-3-Clause、Apache-2.0 及依赖许可证 |
| PDFium | 145.0.7616.0 | PDF解析、文本/图片解码 | pypdfium2 当前平台运行时；本地哈希见下 |
| Pillow | 12.2.0 | 将 PDFium bitmap 编码为 PNG | MIT-CMU |

当前 Work Linux 环境 `libpdfium.so` 的 SHA-256：
`504df0960b4fab9e7c3bce8e4cf944d072a5aba76a5a199609d7addc49656568`。
该动态库、wheel 与许可证文件不复制到源码包；本哈希只用于复现实验身份。

## 后端

| 后端 | 角色 | Stage B 状态 |
|---|---|---|
| PDFium / pypdfium2 | MVP 主后端 | 使用现场固定版本；不下载、不捆绑 |
| Apache PDFBox | 对照或回退 | 仅接口，不下载 JAR、不运行 Java |

项目源码采用 Apache License 2.0。`pypdfium2` 安装元数据列出的 bundled
dependency licenses 中，历史审计的 `agg23=NOASSERTION` 尚无发布级结论；
这不改变项目源码许可证，但正式分发 PDFium 二进制或包含其运行时的 wheel
前仍需完成对应的第三方 notices 审查。

## 明确排除

- LLM、外部生成式 API；
- 云 OCR、本地 OCR 模型；
- PDFium/JAR/可执行文件；
- node_modules、虚拟环境和依赖缓存。
