# GROBID CRF 本地侧车

PaperWright 可把 GROBID 作为科研论文语义证据 provider，但不会采用 GROBID 生成的正文或
Markdown。GROBID 只提交带页码和坐标的题名、作者、单位、摘要、章节、caption、引用与
参考文献等 proposed claims；这些 claim 仍需与 PDFium 原生文字对齐，并受
SourceEvidenceBundle 和 ArticleTree 契约约束。

## 已验证组合

2026-08-22 在 WSL2 x86_64 上完成了以下真实服务验证：

- GROBID `0.9.0-crf`，官方 OCI 镜像索引 digest
  `sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849`；
- OpenJDK 21；
- 12 个 Wapiti/CRF 模型加载成功，0 个模型失败；
- `/api/version`、`/api/isalive` 和 `/api/health` 均通过；
- `Attention Is All You Need` 的 `processFulltextDocument` TEI 输出 XML 可解析，包含
  29 个 `div`、78 个 `p`、5 个 `figure`、3 个 `table`、13 个 `formula` 和
  41 个 `biblStruct`；
- PaperWright `layout-prepare --extraction-profile standard` 成功接入真实 HTTP 输出，
  `grobid-scholarly` provider 状态为 `complete`，保存 847 个 observations；整个证据包
  形成 245 个 claims、38,813 个 alignments 和 8 个 conflicts。

这些数字只证明服务、TEI 坐标、对齐和证据契约已经贯通，不表示 GROBID 的语义判断已
通过质量验收。不同 PDF 会产生不同结果。

## 启动精简 CRF 服务

先准备 GROBID `0.9.0-crf` 发行目录和 Java 21。以下命令适用于发行目录中同时包含
`grobid-home/` 与 `grobid-service/` 的本地安装；路径按实际环境修改：

```bash
export PAPERWRIGHT_GROBID_ROOT=/path/to/grobid-0.9.0-crf
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export LD_LIBRARY_PATH="$PAPERWRIGHT_GROBID_ROOT/grobid-home/lib/lin-64:$PAPERWRIGHT_GROBID_ROOT/grobid-home/lib/lin_arm-64"
export GROBID_SERVICE_OPTS="--add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED"
"$PAPERWRIGHT_GROBID_ROOT/grobid-service/bin/grobid-service"
```

另开终端检查服务：

```bash
curl -fsS http://127.0.0.1:8070/api/version
curl -fsS http://127.0.0.1:8070/api/isalive
curl -fsS http://127.0.0.1:8070/api/health
```

随后让 PaperWright 显式连接本地服务：

```bash
export PAPERWRIGHT_GROBID_URL=http://127.0.0.1:8070
paperwright layout-prepare input.pdf roi-review --extraction-profile standard
```

结果位于 `roi-review/source-evidence/providers/grobid-scholarly.json`。未设置环境变量、服务
不可达或请求失败时，provider 会明确记录为 `unavailable`，不会把失败解释为论文没有
语义结构。

## 资源与运行边界

CRF 版本不加载 DeLFT 模型，但仍是常驻 Java 服务。本次单篇测试后进程 RSS 约为
3.9 GiB，因此不建议在内存紧张的开发机上无任务常驻。PaperWright 不负责自动启动、停止
或下载 GROBID，也不会因为 GROBID 可用就扩大其事实权限。

下一阶段将用冻结的小型论文集分别运行“无 GROBID”和“启用 GROBID”两条粗提取路径，
按 claim 类型测量对齐覆盖、误判和对最终 ArticleTree 决策的实际贡献。
