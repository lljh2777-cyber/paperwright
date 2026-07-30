# 依赖与供应链边界

## bootstrap

运行时依赖仅为 Python 3.10+ 标准库。构建元数据使用 setuptools，但从
源码运行和测试无需联网安装。

## 计划中的后端

| 后端 | 角色 | bootstrap 状态 |
|---|---|---|
| PDFium / pypdfium2 | MVP 计划主后端 | 仅接口，不下载、不加载、不捆绑 |
| Apache PDFBox | 对照或回退 | 仅接口，不下载 JAR、不运行 Java |

正式引入前必须锁定版本、来源、哈希、LICENSE/NOTICE、关键传递依赖和再分发
条件。`agg23=NOASSERTION` 继续阻断正式分发批准，但不阻断本地 bootstrap
设计。

## 明确排除

- LLM、外部生成式 API；
- 云 OCR、本地 OCR 模型；
- PDFium/JAR/可执行文件；
- node_modules、虚拟环境和依赖缓存。
