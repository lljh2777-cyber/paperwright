# PaperWright 粗提取多证据架构 v0.1

> 状态：已接受；E0–E5 纵向原型已实现（2026-08-21）
>
> 范围：born-digital 科研论文的物理与结构证据采集
>
> 非范围：最终 Markdown、模型价格/预算、OCR、自由文本生成

本文取代当前 `fast/standard` 以单一 PDFium 文字视图决定是否枚举视觉对象的目标设计。
旧实现暂时保留为兼容基线，但不应继续围绕其风险阈值调参。

## 1. 决策

PaperWright 不再把“粗提取”定义成一次 PDF→Markdown，也不选择一个外部工具替换
现有后端。粗提取改为 **多提供者证据采集**：每个工具只提交自己擅长的观察或主张，
PaperWright 保留原始结果、统一坐标、建立对应关系并显式暴露冲突。

首选组合是：

1. **PDFium（必需）**：原生字符、字体、对象类型/bbox、页面渲染和按需资产解码；
2. **pdfplumber（默认轻量侧车）**：独立字符/单词视图、line/rect/curve 和表格网格候选；
3. **GROBID（推荐科研语义侧车）**：题名、作者、单位、摘要、章节、正文、图表标题、
   引用与参考文献的论文级 TEI 骨架及坐标；
4. **Docling（可选局部专家）**：只处理冲突页或疑难 Table/reading-order 区域，不作为
   全文事实来源。

Marker、MinerU、PyMuPDF4LLM 和 PDFFigures2 保留为评估或可选 provider 候选，不进入
首个默认实现。

核心不是“多数投票”。原生字符仍是正文事实；GROBID/Docling 的角色和顺序属于可拒绝
的结构主张；表格、公式和视觉区域可以在 Markdown 中采用页面裁剪图，而不要求任何
模型重写原文。

## 2. 为什么当前粗提取不够

当前 `PhysicalDocument` 只能表达一个 backend 的单一解释。`standard` 先运行
`text-only-fast`，再由基于这份文字视图的风险规则决定是否执行完整对象遍历。这形成了
循环依赖：没有枚举 image/vector，就可能没有风险信号；没有风险信号，就永远不会枚举
image/vector。

两个已知失败样本都实际走成了纯文字：

| 样本 | 页数 | `standard` 缓存中的非文字对象 | PDFium 完整遍历发现 | text-only / full |
|---|---:|---:|---|---:|
| U02 Norwood | 8 | 0 | 6 个 image、566 个 vector | 455 ms / 888 ms |
| U03 goat casein | 17 | 0 | 11 个 image、88 个 vector | 664 ms / 3034 ms |

以上时间是 2026-08-21 在当前 WSL 环境的单次只读实测，只用于说明量级，不是正式性能
基准。U02 第 6、7 页后来丢失的科研图，在完整遍历中都能看到原生 image；问题发生在
证据采集阶段，而不是视觉模型能力不足。

因此，产品默认路径必须满足：**先完成低成本的全页对象清单，再决定哪些对象需要解码、
哪些区域需要专家分析。** `text-only` 可以继续作为诊断/极速选项，但不得再被称为完整
的标准证据。

## 3. 工具调研与取舍

本表依据 2026-08-21 的官方项目说明；外部能力和许可证在真正接入时仍需固定版本复核。

| 工具 | 擅长 | 主要限制 | PaperWright 决定 |
|---|---|---|---|
| [PDFium / pypdfium2](https://pypdfium2.readthedocs.io/) | 快速原生文字、对象、位图和页面渲染；当前已有稳定适配 | 不提供科研论文语义；当前快速路径跳过对象枚举 | 保留为物理事实主后端，拆分 inventory 与 materialize |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | char/word 坐标、line/rect/curve、可调表格网格和可视化；MIT、CPU | 阅读顺序和无框表格启发式会产生假阳性，不理解论文角色 | 默认轻量侧车，只发观察和候选，不直接写表格 |
| [GROBID](https://github.com/grobidOrg/grobid) | 专门面向科研论文；输出结构化 TEI、引用关系和可选 PDF 坐标；默认 CRF 可运行在普通硬件 | Java 服务部署；表格/视觉资产保真不是强项；语义仍可能误判 | 推荐语义侧车，提供文档骨架和锚点，不提供正文真值 |
| [Docling](https://github.com/docling-project/docling) | reading order、布局、TableFormer、公式/代码分类、带 provenance 的统一文档树；MIT，支持 CPU | 模型下载与运行资源明显高于轻量解析器；仍会进行模型派生识别 | 仅对冲突页/表格 ROI 调用，可本地或外部 provider |
| [Marker](https://github.com/datalab-to/marker) | 科研文档、表格、公式、图片和 JSON block tree；按需 OCR/VLM | PyTorch/VLM 依赖；代码与模型权重许可证边界不同；端到端输出会重做正文 | 用作离线对照或显式外部候选，不作为核心依赖 |
| [MinerU](https://github.com/opendatalab/MinerU) | 丰富的中间 JSON、布局、公式、表格、跨页表格和视觉调试产物 | 官方 CPU pipeline 建议至少 16 GB RAM/20 GB 磁盘；许可证含附加条款 | 用作高能力评估基线或外部 provider，不进入默认安装 |
| [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/) | 很轻的 Markdown/JSON、bbox、表格、图片和混合 OCR | PyMuPDF 为 AGPL/商业双许可证，与本项目 Apache 分发边界需专门审查 | 不纳入核心；可在开发评估中作为外部进程比较 |
| [PDFFigures2](https://github.com/allenai/pdffigures2) | 科研论文 Figure/Table、caption、section title 与 bbox | Scala/sbt，重点校准于计算机科学论文，项目和规则较旧 | 作为 Figure/caption 回归基线研究，不优先产品接入 |

两个重要参照：

- [GROBID 官方说明](https://grobid.readthedocs.io/)表明其 full-text 流程本身就是多个
  文档分割、正文、图表、引用和参考文献模型的级联，而不是一个通用 PDF 文本抽取器；
- [S2ORC doc2json](https://github.com/allenai/s2orc-doc2json)已经证明“科研语义解析器输出
  → 自有规范文章模型”是可行边界。PaperWright 与之不同之处是继续保留字符、bbox、
  页面资产和多 provider 冲突，而不是直接相信 TEI。

### 3.1 本地轻量试验

在隔离临时环境使用 pdfplumber 0.11.10 对 U02/U03 做了只读盘点：

- U02 用约 1.4 秒枚举 8 页字符和图形对象，识别出第 3、6、7 页各有原生 image；
- U03 用约 1.2 秒枚举 17 页，原生 image 页与 PDFium 完整遍历一致；
- pdfplumber 默认线框策略在 U02 第 3 页提出一个表格区域，但其单独恢复出的 cell 内容
  不可靠；纯文字对齐策略甚至会把大半页误认为表格。

这验证了它适合做 **独立几何证据和高召回候选源**，不适合直接成为语义表格写出器。

## 4. 目标数据流

```text
source PDF + sha256
    │
    ├── PDFium inventory ── native characters / object bounds / page render
    ├── pdfplumber ───────── words / lines / rects / curves / table proposals
    ├── GROBID ───────────── scholarly structure / citations / TEI coordinates
    └── specialist requests ─ Docling on selected pages or ROIs
              │
              ▼
      immutable provider snapshots
              │
              ▼
      coordinate + text alignment
              │
              ▼
       SourceEvidenceBundle
       ├── observations
       ├── alignments
       ├── semantic claims
       ├── agreements
       ├── conflicts
       └── missing capabilities
              │
              ▼
      DocumentPlan / PaperRecipe
              │
              ▼
     deterministic ArticleTree compiler
```

禁止在粗提取层先各自生成 Markdown 再比较。Markdown 已经丢失大量 bbox、层级、单元格、
对象来源和不确定性，无法作为 provider 间对齐格式。

## 5. 信任与融合规则

### 5.1 三种记录必须分开

- **Observation**：工具直接观察到的字符、对象、线、bbox、页面像素或 XML 节点；
- **Claim**：工具对 observation 作出的 `abstract`、`body`、`table`、reading order 等判断；
- **Decision**：PaperRecipe/编译器最终采用、排除、排序或渲染的动作。

`Claim` 不得覆盖 `Observation`，`Decision` 必须引用二者的稳定 ID。provider 原始输出按
源 PDF 哈希和 provider 身份保存，后续标准化不能原地改写。

### 5.2 权威范围

| 内容 | 默认权威 | 其他来源的作用 |
|---|---|---|
| 论文正文字符 | PDFium 原生字符，pdfplumber 独立字符用于核对 | GROBID/Docling 只能建议 span、顺序或异常 |
| 原生 image/vector 存在性 | PDFium inventory + 页面 raster | pdfplumber 用对象类别和图形密度交叉检查 |
| 题名/作者/单位/摘要/章节/参考文献 | GROBID claim | 几何、字体和文本模型进行校验或拒绝 |
| Table 区域 | caption anchor + PDFium/pdfplumber 几何的联合证据 | Docling 只在冲突区域提供结构主张 |
| Figure 区域 | 原生对象/raster residual + caption relation | GROBID/Docling 提供语义边界候选 |
| display equation | 字体/几何/孤立行与 raster 证据 | 专家模型可提出区域，但首版仍渲染为图 |

### 5.3 不做多数投票

两个解析器给出相同错误不构成真值。融合器应执行可解释检查：

- 规范化文本是否相同，差异能否定位到字符和 bbox；
- 两个区域 IoU、包含关系和基线是否相容；
- GROBID 的段落/章节是否能由原生连续 span 覆盖；
- Table/Figure claim 是否有 caption、对象或 raster residual 支撑；
- provider 是否在该能力上明确 `unavailable`，而不是把空数组解释为“没有”。

冲突不能自动解决时，写入 EvidenceBundle；由低价文本模型读取紧凑冲突摘要，必要时才
请求局部视觉判断。核心不维护模型价格或 token 预算。

## 6. 最小契约边界

首版不要立即把所有 provider 元素塞进 `PhysicalDocument`。保留现有对象作为 PDFium
规范物理视图。E1 最初新增 `paperwright-source-evidence-v0.1`；E4 为哈希绑定的局部专家
请求升级为兼容读取旧版的 `paperwright-source-evidence-v0.2` 索引：

```json
{
  "contract_version": "paperwright-source-evidence-v0.2",
  "source_sha256": "...",
  "providers": [
    {
      "provider_id": "pdfium-native",
      "version": "...",
      "capabilities": ["native_text", "object_inventory", "render"],
      "snapshot_path": "providers/pdfium-native.json",
      "snapshot_sha256": "...",
      "status": "complete"
    }
  ],
  "alignments_path": "alignments.json",
  "claims_path": "claims.json",
  "conflicts_path": "conflicts.json",
  "specialist_requests_path": "specialist-requests.json",
  "status": "conflicted"
}
```

必要约束：

- provider observation ID 在同一快照中稳定且唯一；
- 所有 bbox 既保存 provider 原坐标，也保存到 PaperWright 坐标的变换；
- alignment 保存算法版本、双方 ID、文本/几何分数，不能吞并原元素；
- claim 必须列出证据 ID、能力名和 provider；
- 缺失 provider、超时或不支持必须显式记录 `unavailable/degraded`；
- 同一源文件和固定 provider 版本的快照、对齐和冲突输出可确定性重放。

## 7. 实施顺序

### E0：修复必需物理清单

**状态：已完成。** `PDFiumBackend.extract_inventory()` 现以 TextPage 提取原生文字，同时
遍历所有页的 image/vector 类型与 bbox。image 元素以
`asset_materialization=deferred` 明确表示尚无位图资产；`extract_hybrid()` 只在升级页
解码图片，其他页继续保留完整 bounds inventory。显式 `fast` 仍保持纯文字诊断语义。

U02/U03 的只读实现验收中，inventory 与 full 的逐页 image/vector 计数完全相同，且
inventory 均产生 0 个图片资产、图片解码时间为 0。U02 第 3、6、7 页分别可见 2、2、2
个原生 image，不再依赖旧 text-only 风险路由才能发现。

1. 将 PDFium 的“对象枚举”与“位图解码/资产写出”拆开；
2. 产品标准路径对每页枚举 text/image/vector 的类型与 bbox；
3. 只有最终采用或需要检查的对象才解码位图、渲染高分辨率 ROI；
4. 将旧 `standard=text-only unless risky` 标为 legacy，不再作为默认质量路径。

E0 完成后，即使所有可选 provider 都缺失，U02 第 6、7 页也不能再表现为“无视觉对象”。

### E1：建立 provider snapshot 与对齐层

**状态：已完成。** 新增 `paperwright.source_evidence` 与
`paperwright-source-evidence-v0.1` 索引。`layout-prepare` 现在写入哈希绑定的 PDFium
provider snapshot、逐 observation 的原坐标/PaperWright 坐标、规范文本指纹、到
PhysicalDocument element 的 identity alignment，以及独立的 claims/conflicts 文件；
`layout-apply` 会重新验证整条哈希链、坐标变换、ID 唯一性与 alignment 守恒。旧
PhysicalDocument 继续作为下游兼容视图。

1. 新增 `SourceEvidenceBundle` 索引和严格校验器；
2. PDFium 先以 provider snapshot 形式接入；
3. 加入文本 fingerprint、bbox 映射和 observation alignment；
4. 旧 `PhysicalDocument` 通过适配器继续供下游使用，不立即重写全部流水线。

### E2：接入 pdfplumber

**状态：已完成。** `standard/forensic` 现在默认运行锁定的 pdfplumber 0.11.10，保存
char/word、line/rect/curve、image 的独立 observations，并按文字/类型/几何与 PDFium
元素建立可拒绝 alignment。默认线框表格检测只写 `table_region` proposed claim，明确
`direct_markdown_authority=false`，不恢复 cell 文本、不直接写 Markdown。显式 `fast`
仍只保留 PDFium 证据。U02 产生唯一的第 3 页表格 claim；U03 没有表格 claim，避免了
此前文字对齐策略的大面积假阳性。

输出 char/word、line/rect/curve、image bbox 和 table/cell proposal。表格算法只产生 claim；
caption 引导的 crop 内重跑策略可以改变参数，但必须记录参数与结果。

### E3：接入 GROBID

**状态：已完成。** 新增可选本地 HTTP provider `grobid-scholarly`。设置
`PAPERWRIGHT_GROBID_URL` 后，PaperWright 请求 `processFulltextDocument` 的固定 TEI
coordinate 集，并将 title/author/affiliation/abstract/section/paragraph/figure-table
caption/reference/citation 作为 proposed claims；每个 claim 必须先有合法 page+bbox
observation，并尝试对齐 PDFium 原生文字。TEI 文本没有直接正文权限。未配置、请求失败
或服务不可达时，索引明确保存 `status=unavailable` 及原因，而不是空结构。当前环境没有
运行 GROBID 服务，因此 HTTP 端到端留待服务部署验证；TEI 解析、角色约束与坐标对齐已
用固定离线夹具覆盖。

以可选本地 HTTP provider 接受固定 TEI 与坐标输出。第一阶段只使用：front matter、
abstract、section、paragraph、figure/table caption、references 和 inline citation links。
TEI 文本必须重新对齐原生字符，无法对齐的节点不得直接进入 ArticleTree。

### E4：局部 Docling provider

**状态：已完成。** `source_evidence` 先从多源证据确定性产生三类 open conflict：未获独立
支撑的 Table 边界/结构提案、GROBID 与原生文字的显著阅读顺序倒置、原生视觉对象与
raster residual 的严重不一致。每个 conflict 生成仅含 page/ROI 和能力名的
`paperwright-specialist-requests-v0.1` 请求。只有显式设置
`PAPERWRIGHT_DOCLING_ENABLED=1` 且存在请求时才加载可选 Docling，并通过官方
`page_range` 对选中页逐页运行；结果只保存请求 ROI 内的 DoclingDocument item、表格
data 和 `page_no/bbox/charspan` provenance。代码从不请求或接收 Docling Markdown。

未启用、未安装、页面转换失败与“没有冲突所以未请求”分别写入 provider diagnostics；
请求状态为 `not_run/completed/failed`。存在 open conflict 时 bundle 顶层状态固定为
`conflicted`，即使专家已返回 proposed evidence 也不会伪装成已自动解决。当前环境未安装
Docling，因此真实模型推理尚未实测；导出 JSON 的解析、ROI 过滤、原生对齐、结构 claim
和 provenance 已用固定离线夹具覆盖。

U02/U03 的实现验收中，U02 只为第 3 页已知 Table 生成 1 个局部请求；U03 没有满足阈值
的冲突并生成 0 个请求。未启用 Docling 时前者为 `conflicted + not_run`，后者明确记录
`docling_not_requested_no_conflicts`，没有扩大到全文推理。

只对下列冲突请求运行：多 provider 阅读顺序冲突、Table 边界/结构冲突、原生对象与
raster residual 严重不一致。保存 DoclingDocument 子集和 provenance，不接收其全文
Markdown 作为最终内容。

### E5：交给论文级 Recipe

模型读取 document skeleton、页面摘要和冲突清单，按需展开原生 span 或 ROI；输出受限
PaperRecipe。Recipe 可以 classify/exclude/split/merge/order/bind/render，但不能生成或
替换论文正文。

**状态：纵向原型已完成。** 当前先实现确定性 baseline producer，写出
`paperwright-paper-recipe-v0.1`；它只保存原生元素 ID、证据引用、规范化 bbox 和受限
动作，不保存正文或 Markdown。`paperwright-article-tree-v0.1` 编译器为每个
PhysicalDocument 元素生成唯一叶节点，以 source text SHA-256 代替正文副本，并验证
完整覆盖、无重复、父子引用、输入哈希和确定性重放。

Recipe 已接到 `layout-apply`：provider Table claim 投影成局部图片渲染，区域内原生数字
不再同时进入 prose；未被视觉布局覆盖的原生 image 会补成 Figure；caption 角色必须有
原生 `Figure/Table N` 锚或 provider semantic claim；首页小型 raster-only 出版标记和
底部出版 vector 家具可显式排除。模型生成 Recipe 的运行时仍未开放，后续 producer 必须
复用同一契约、能力白名单和验证器，不能获得额外正文权限。

U02/U03 回放结果：U02 第 3 页 Table action 覆盖 487 个物理对象，原开发布局投影后这些
对象在 text region 中剩余 0 个；第 6、7 页补回 4 个原生 Figure。U03 首页 raster
residual `RV0002` 被 bbox-scoped furniture action 排除，旧 `r-g6` 伪 Figure 转为
exclude，citation/license 的旧 `r-g7` 从 caption 降为 margin；ArticleTree 对 1759 个
物理元素完整守恒，`generated_text_count=0`。这些是已知 U02/U03 的开发回放，不是新的
未见论文泛化结论。

## 8. 首轮验收

粗提取层的验收对象是“是否提供了足够且诚实的证据”，不是 JSON 是否通过 schema，
也不是某个工具直接生成的 Markdown 看起来是否整齐。

在 U02/U03 上先冻结以下检查：

1. 每页都有完整 object inventory；provider 未运行与“该页没有对象”可区分；
2. U02 第 3、6、7 页的原生 image 均进入证据，后两页不能再漏掉科学 Figure；
3. U02 的 Table caption 会触发表格区域 claim；低质量 cell 恢复不会落入正文数字流；
4. U02 首页的作者单位/邮箱与正文必须形成可供 Recipe 分离的不同 semantic claim；
5. U03 首页 `check for updates` 只能是视觉观察或 furniture/logo claim，不能因为邻近
   citation/license 文本就被粗提取层固化成 Figure-caption；
6. U03 abstract 的原生 span 必须完整保留，编辑信息只能作为冲突角色存在；
7. 任一高层节点都能回到 provider observation、page、bbox 和 source hash；
8. provider 发生冲突时状态为 `conflicted/degraded`，不能仍报告质量已接受。

本组验收和 ArticleTree/PaperRecipe 纵向原型已经完成。下一阶段不新增视觉关系 prompt
补丁，而是让 ArticleTree 成为 ArticleModel 之前的规范结构输入，并在新冻结论文上验证
首页家具、Table 图片回退和原生 Figure 补全的误伤率。
