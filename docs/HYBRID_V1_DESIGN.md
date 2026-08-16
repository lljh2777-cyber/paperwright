# PaperWright Hybrid v1 设计

> 状态：目标架构已定稿（2026-08-15）  
> 范围：产品边界、单一流水线、AI 权限、核心产物与迁移原则  
> 非范围：本文件不冻结具体 JSON Schema，也不授权立即删除兼容代码

本文定义 PaperWright 下一阶段的目标架构。它是后续设计和实现的优先依据；
现有文档或代码与本文冲突时，应先按本文制定迁移方案，而不是继续扩展冲突路径。

---

## 1. 产品定义

PaperWright 是一个面向 **born-digital 科研论文**的、可追溯的混合文档重建器。
它以 PDF 原生对象为事实来源，以低价纯文本模型进行结构规划、关系判断和长尾程序
合成，以视觉模型回答无法从物理证据可靠确定的局部问题，最终生成图文并茂、阅读
顺序正确的 Markdown，以及可以回到原 PDF 的 Article Model、Reader 和证据索引。

这里的关键词含义固定如下：

- **科研论文**：优先覆盖标题、作者与单位、摘要、章节、正文、Figure、Table、
  display equation、caption、脚注、参考文献和补充材料等学术论文结构；
- **born-digital**：默认输入具有可用的原生文字层。扫描件和 OCR 不是 Hybrid v1
  的核心能力；
- **重建**：恢复逻辑阅读结构和图文关系，不追求逐像素复刻 PDF 页面；
- **混合**：确定性规则、文本模型、视觉模型和受限程序合成服务于同一条流水线，
  不是四种相互独立的产品模式；
- **可追溯**：每个正文块、视觉资产和关系都能定位到 page、bbox、element ID、
  输入哈希和处理产物；
- **非转写**：只要原生文字可信，模型不得重新生成论文正文。

### 1.1 Hybrid v1 的目标

1. 将复杂科研 PDF 重建为适合连续阅读的图文 Markdown；
2. 保留 Figure、Table 和 display equation 的视觉保真及原始来源；
3. 对多栏、跨栏视觉对象、图注、跨页续接等长尾结构进行局部 AI 消歧；
4. 让模型产物可以校验、拒绝、重放和替换；
5. 用稳定 ID 和受限操作阻止静默增字、删字、重复和来源断裂；
6. 让简单页面与复杂页面使用同一条编译流水线和同一个规范文章模型；
7. 记录模型、prompt、输入、输出、usage、延迟和校验结果，支持离线比较不同模型。

### 1.2 Hybrid v1 的非目标

以下能力不属于当前核心范围：

- 通用 PDF、财报、合同、表单、书籍和历史档案的全面支持；
- 自研或内置 OCR；
- 默认将公式转写为 LaTeX；
- 默认将表格恢复为语义单元格；
- 像素级版面或字体样式复刻；
- 任意 Agent/Python 代码执行；
- 模型价格维护、费用估算和预算限制；
- RAG 切块、向量化、问答和知识库管理；
- Web 服务或图形界面。

外部 OCR、表格或公式识别器以后可以作为 provider 接入，但其模型派生内容必须与
原生 PDF 证据使用不同的信任标记，不能伪装成原生字符。

---

## 2. 命门原则

### 2.1 只有一条产品流水线

Hybrid v1 不再发展 direct 与 hybrid 两套最终写出路径。所有论文都经过证据构建、
计划、消歧、校验和 Article Model 编译；简单页面可以完全由确定性规则完成，但仍
属于同一条 hybrid 流水线。

删除 direct 产品方向不等于删除规则内核。PDFium 提取、几何分析、文字重建、
候选生成和质量检查仍是整个系统的基础。

### 2.2 原生内容是事实，AI 输出是建议

- PDF 原生文字和对象是默认事实来源；
- 模型只能引用稳定 ID、声明关系或提出操作；
- 模型建议必须经过 PaperWright 校验；
- 校验通过只能证明契约成立，不能被描述为模型语义必然正确；
- 任何无法可靠消歧的内容都应显式进入 `human_required`，不得猜测后继续。

### 2.3 AI 不负责抄写论文

文本模型和视觉模型不得重新转写已有原生正文，不得总结、润色、翻译、补全或修正
科学内容。第一版操作集中不提供通用的 `set_text`、`replace_text` 或自由文本生成
能力。

允许的文字变化必须是可验证的结构变换，例如：

- 合并两个具有来源的相邻块；
- 去除具有几何和字符证据的换行连字符；
- 在源块边界插入空格或段落边界；
- 调整已有块的阅读顺序；
- 按 source span 分割已有块。

### 2.4 AI 优先输出关系，不优先输出坐标

模型优先在候选 ID 之间选择、排序、分组和绑定：

```text
block b17 belongs_to caption c3
caption c3 belongs_to visual v2
block b21 continues b20
region r4 comes_after r2
candidate c8 split_at separator s3
```

只有现有候选无法覆盖真实区域时，才允许模型提出新 bbox 或分割线；所有新坐标必须
位于已确认 Content ROI 内，并在应用前吸附到原生元素或栅格证据边界。

### 2.5 可观测不等于预算管理

核心应记录供应商原样返回的 usage、模型标识、prompt/bridge 版本、延迟、重试、
校验结果和产物哈希。核心不维护价格表、不估算费用、不限制预算，也不假设不同
供应商的 token 单位完全等价。质量、token 和延迟的聚合比较属于 evaluation 层。

---

## 3. 单一 Hybrid 流水线

目标流水线固定为：

```text
input PDF
    │
    ▼
extract ──→ build evidence ──→ plan ──→ resolve
                                         │
                                         ├─ deterministic operations
                                         ├─ text-model decisions
                                         ├─ constrained visual questions
                                         └─ optional PaperRecipe
                                         │
                                         ▼
                                      validate
                                         │
                        ┌────────────────┼────────────────┐
                        ▼                ▼                ▼
                     invalid       human_required    accepted /
                                                     suspicious
                                                         │
                                                         ▼
                                                  Article Model
                                                         │
                                      ┌──────────────────┼──────────────┐
                                      ▼                  ▼              ▼
                                  Markdown            Reader         Assets
```

### 3.1 Extract

PDFium 负责读取原生文字、bbox、字体、位图、矢量对象和页面元数据。提取阶段不得做
模型推理，也不得把推断的语义写回物理文档。

### 3.2 Build evidence

确定性代码从 PhysicalDocument 构建用于判断的证据：

- Content ROI；
- 原生文字块和稳定 ID；
- 字体、间距、对齐与重复元素特征；
- 高召回布局候选和 separator；
- Figure/Table caption 候选；
- 栅格 residual 和视觉候选；
- 页面风险信号；
- 页间连续性和重复页眉页脚信号。

证据只描述“观察到了什么”，不得将启发式分类冒充最终语义。

### 3.3 Plan

规划阶段生成论文级 `DocumentPlan`，描述页面分组、布局假设、适用的标准策略、待解决
问题和需要展开的局部证据。普通论文可以由确定性规划器完成；出现冲突时，文本模型
基于紧凑证据摘要进行规划。

规划阶段不生成 Markdown，不修改正文，也不要求一次性完成全部 bbox。

### 3.4 Resolve

消歧阶段把 DocumentPlan 转换成可执行的 ReviewPlan。处理顺序是：

1. 运行通用科研论文规则和标准 Recipe；
2. 对局部文本关系使用结构化文本判断；
3. 对必须看页面的问题生成受限视觉问题；
4. 仅在声明式计划无法表达跨页、循环或条件逻辑时生成 PaperRecipe；
5. PaperRecipe 执行后仍只产出 ReviewPlan，不直接写 Article Model 或 Markdown。

### 3.5 Validate

所有来源的 ReviewPlan 使用同一组验证原则。硬契约失败时禁止编译 Article Model；
质量信号不充分时进入 `suspicious` 或 `human_required`，不得通过弱化校验器来换取
“成功”。

### 3.6 Compile and project

经过验证的 ReviewPlan 与原生证据一起编译成唯一规范 Article Model。Markdown、
Reader 和视觉资产都是 Article Model 的确定性投影；不得再为所谓 direct 模式维护
另一套正文重建和最终写出规则。

---

## 4. 科研论文内容模型

Hybrid v1 优先支持以下角色：

```text
title
authors
affiliations
abstract
keywords
heading
body
list
figure
table
equation
caption
footnote
references
supplementary
header
footer
margin
other
unknown
```

角色集合是科研论文领域约束，不承诺覆盖任意办公文档语义。`unknown` 是合法的中间
状态，但在最终 Article Model 中是否允许保留，应由质量策略明确决定。

Figure、Table 和 display equation 默认生成视觉资产。原生文字层的降级文本继续保留
在 Article Model 或 Reader 中；它是否投影进阅读版 Markdown，由输出 profile 决定，
不得打断正文阅读顺序。

---

## 5. AI 的职责与权限

### 5.1 结构规划者

文本模型可以依据文档级摘要判断：

- 页面属于标题页、正文、参考文献还是补充材料；
- 页面采用单栏、双栏、三栏还是混合结构；
- 哪些页面可以共享同一策略；
- 哪些局部问题需要展开证据或视觉确认；
- 声明式 ReviewPlan 是否足够，还是需要 PaperRecipe。

### 5.2 关系判断者

文本模型可以判断已有对象之间的有限关系：

- continuation；
- heading/body/caption/reference 等角色；
- join/split/reorder；
- caption continuation；
- 参考文献条目的边界。

它只能输出 schema 允许的枚举、ID 和操作，不能输出替代正文。

### 5.3 局部视觉判断者

视觉模型只回答从物理对象和文字证据无法可靠确定的问题。视觉任务优先设计为候选
选择或关系判断，例如：

- `column_structure`；
- `reading_order`；
- `region_role`；
- `caption_owner`；
- `figure_or_table_boundary`；
- `split_candidate`。

问题必须带允许的回答集合，并允许返回 `unresolved`。视觉模型不得因为候选不足而
转写图中文字或论文正文。

### 5.4 长尾程序合成者

PaperRecipe 是声明式计划无法表达时的最后手段，适用于：

- 同一论文多种栏结构的条件化处理；
- 跨页遍历和连续性判断；
- 重复侧栏或出版版式模式；
- 需要聚类、循环和多步关系推导的布局；
- 补充材料与正文采用不同规则。

PaperRecipe 使用 Python 风格受限 DSL，但只允许只读查询和结构化 `emit`。它不得
访问文件、网络、环境变量、时间、随机数或任意 Python 对象。

---

## 6. 核心产物

本节定义逻辑职责。具体字段、schema 文件名和兼容版本在实现前单独评审。

### 6.1 EvidenceBundle

EvidenceBundle 是供规划、模型和 Recipe 使用的只读证据视图。它不应复制一份新的
全文事实库，而应通过哈希和稳定 ID 引用现有产物：

- PhysicalDocument；
- extraction cache；
- LayoutTask 和候选特征；
- 页面缩略图及局部 ROI；
- layout risk；
- 确定性质量信号。

EvidenceBundle 应支持逐级展开：先提供文档/页面摘要，再按任务提供局部块，最后才
提供必要的 ROI 图片。模型不应默认接收全文和全部页面图像。

### 6.2 DocumentPlan

DocumentPlan 是论文级策略，至少表达：

- 输入与 EvidenceBundle 哈希；
- 页面分组；
- 每组布局假设；
- 适用的标准策略；
- 局部待解决问题；
- 所需视觉问题；
- 是否需要 PaperRecipe；
- 无法自动处理的原因。

DocumentPlan 不保存最终正文，也不直接替代 FinalLayout。

### 6.3 ReviewPlan

ReviewPlan 是所有规则和模型共同产出的规范操作计划。目标操作集合为：

```text
classify
group
split
reorder
join_blocks
resolve_hyphenation
bind_caption
exclude_furniture
render_visual
request_visual
```

ReviewPlan 必须只引用 EvidenceBundle 中存在的稳定 ID，记录每个操作的理由、来源和
执行者，并明确未解决问题。

迁移初期不要求立即废弃 `FinalLayout` 和 `TextReview`。ReviewPlan 可以先作为逻辑
编排层，由适配器投影到现有布局和文本契约；当真实论文回归证明统一操作模型稳定后，
再决定是否合并物理 schema。

### 6.4 PaperRecipe

PaperRecipe 是生成 ReviewPlan 的可选程序产物，必须保存：

- DSL 和运行时版本；
- 输入 EvidenceBundle 哈希；
- 代码原文和代码哈希；
- 使用的 API/emit 能力；
- 执行限制；
- 执行 trace；
- 生成的 ReviewPlan 哈希；
- 重放校验结果。

第一版允许的能力与 ReviewPlan 操作集一致，不提供正文替换、任意删除和任意坐标
裁剪能力。

### 6.5 Article Model

Article Model 继续作为规范文章来源，保存：

- 阅读顺序中的文章块；
- source span 和可见文本指纹；
- Figure/Table/Equation 视觉资产；
- caption、continuation 等关系；
- 反向 provenance；
- 输出 profile 所需的替代表示。

Markdown 和 Reader 必须从同一 Article Model 确定性生成。

---

## 7. 状态与失败语义

最终处理状态定义如下：

| 状态 | 含义 | 是否允许自动发布正常结果 |
|---|---|---|
| `accepted` | 硬校验通过，质量检查未发现需要阻断的问题 | 是 |
| `suspicious` | 硬校验通过，但存在需要向用户显式展示的质量风险 | 仅以降级状态发布 |
| `invalid` | 契约、守恒、哈希、引用或确定性校验失败 | 否 |
| `human_required` | 信息不足或歧义无法可靠自动解决 | 否，等待人工决定 |

状态不能互相伪装：

- `invalid` 不能通过删除失败项变成 `accepted`；
- `human_required` 不能由模型猜测后静默继续；
- `suspicious` 必须保留问题定位和证据；
- 模型调用失败不等于内容处理成功；
- “校验器通过”不得在用户文案中扩大为“语义完全正确”。

### 7.1 硬校验

PaperWright 必须尽可能确定性证明：

- 输入和所有上游产物哈希匹配；
- 所有引用的 page/block/element/region ID 存在；
- 有效正文没有无理由遗漏或重复；
- 模型没有创造新的论文正文；
- bbox 有限、为正、在页面和 ROI 边界内；
- 阅读顺序唯一且完整；
- caption、视觉资产和父子关系引用有效；
- 输出文件、Reader、manifest 与 Article Model 一致；
- PaperRecipe 在同一输入上可确定性重放。

### 7.2 质量判断

以下问题不能仅靠硬校验证明，只能由质量信号、受限模型判断或人工复核解决：

- 阅读顺序是否符合作者语义；
- 标题层级是否正确；
- caption 的真正归属；
- 跨页块是否属于同一段；
- 某区域是正文、公式、表格还是 Figure；
- 候选边界是否完整覆盖视觉对象且没有侵入正文。

---

## 8. 现有契约复用与迁移边界

Hybrid v1 不从空白仓库重写。以下能力是保留资产：

| 现有资产 | Hybrid v1 定位 |
|---|---|
| `PhysicalDocument` | 原生物理事实层，保留 |
| extraction cache | 可复现提取证据，保留 |
| `LayoutTask` | EvidenceBundle 的页面布局证据，保留并适配 |
| `FinalLayout` | 当前布局决策契约，迁移期继续使用 |
| `ArticleModel` | 规范文章模型，保留并成为所有路径唯一汇合点 |
| `Reader` | 对外来源与关系索引，保留 |
| `TextTask` / `TextReview` | 当前文本操作契约，迁移期继续使用 |
| `synthesize.py` | PaperRecipe 运行时的最小原型，扩展而非重写 |
| `validate-*` | 信任边界，保留并扩展 |
| manifest/hash/atomic publish | 产物完整性边界，保留 |
| `routing.py` | 未来 plan/resolve 的早期实现，重构而非旁路 |

迁移期间必须遵守：

1. 不在 direct writer 上增加新能力；
2. 不立即删除 direct 兼容行为；
3. 新流水线先通过适配器复用现有契约；
4. 用真实论文回归证明统一路径后，再删除重复实现；
5. 任何 schema 变更继续遵守契约版本、fixture、测试和迁移文档要求；
6. 兼容入口不能成为长期维护的第二套语义实现。

---

## 9. 退出和延后事项

以下内容退出 Hybrid v1 核心路线图：

- direct 模式的新功能开发；
- 在核心包中维护模型价格；
- CLI token/费用预算限制；
- 以预算作为确定性路由信号；
- 默认语义表格恢复；
- 默认公式 LaTeX 转写；
- 扫描 PDF 的内置 OCR；
- Web/API 产品化。

以下内容保留，但重新定义：

- **usage 记录**：保留供应商原始 token 字段和调用事实，不计算费用；
- **L0**：保留为同一 hybrid 流水线中的确定性执行层，不再代表 direct 产品；
- **路由**：依据证据、风险和验证结果选择处理能力，不依据价格或预算；
- **规则沉淀**：AI Recipe 只能形成规则候选，必须经过回归和人工评审后进入标准库；
- **外部模型**：保持可替换，只通过稳定任务和产物契约接入。

---

## 10. 目标实现顺序

后续工作按以下依赖关系推进：

1. 建立真实科研论文的重构前质量基线；
2. 引入唯一的 HybridPipeline 编排入口；
3. 让所有输出汇合到 Article Model 和统一 writer；
4. 定义 EvidenceBundle 和 DocumentPlan 的最小落盘契约；
5. 将视觉协议从自由整页画框改为候选关系判断优先；
6. 定义统一 ReviewPlan 及到现有契约的适配器；
7. 扩展 PaperRecipe v0.1 的只读 API 和 emit 操作；
8. 让质量失败回流到局部 resolve，而不是重跑整篇论文；
9. 从已验证 Recipe 中提取规则候选并运行回归评审。

当前实施状态：第 1 项质量基线与第 8 项所需的局部 issue/Completeness 回流基础已经
完成，契约见 [BASELINE_V0.1](BASELINE_V0.1.md)、
[COMPLETENESS_GATE_V0.1](COMPLETENESS_GATE_V0.1.md) 和
[ISSUE_ROUTING_V0.1](ISSUE_ROUTING_V0.1.md)。现有编排器仍是迁移适配器，下一步应把
这些能力汇合到唯一 HybridPipeline，而不是继续扩展 direct 路径。

视觉协议第 5 项也已完成第一版：模型不再修改 bbox，只输出候选 group、role、order 和
caption-of，确定性编译器生成 FinalLayout。见
[VISUAL_RELATIONS_V0.1](VISUAL_RELATIONS_V0.1.md)。

PaperRecipe 的扩展晚于 EvidenceBundle、DocumentPlan 和 ReviewPlan。否则 AI 会围绕尚未
稳定的输入和输出接口生成代码，把当前的协议分裂固化为新的兼容负担。

---

## 11. Hybrid v1 完成标准

满足以下条件后，才可以认为 Hybrid v1 架构完成：

- 用户只需一个正式转换入口，不需要理解 direct/hybrid；
- 简单页和复杂页经过同一条流水线；
- 所有正常输出都由 Article Model 统一投影；
- 文本和视觉模型只接收任务所需的逐级展开证据；
- 视觉模型主要输出候选关系，任意 bbox 是受控例外；
- 规则、文本判断、视觉判断和 PaperRecipe 产出同一逻辑 ReviewPlan；
- AI 无法通过任何公开操作无来源地创建或改写正文；
- `accepted`、`suspicious`、`invalid`、`human_required` 对用户可见且语义稳定；
- 任意模型产物都能定位到输入、prompt/bridge 版本、原始响应、规范化和校验记录；
- 至少在约定的真实科研论文回归集上证明没有相对基线的重大质量退化；
- direct 兼容代码不再承载独立的新语义或新功能。

---

## 12. 已定案与仍待设计

### 12.1 已定案

- 严格聚焦 born-digital 科研论文；
- 只发展一条 hybrid 产品流水线；
- Article Model 是所有正常输出的规范来源；
- AI 不转写可靠原生正文；
- 文本模型负责规划、关系判断和长尾程序合成；
- 视觉模型只处理局部、受约束的视觉问题；
- PaperRecipe 是最后手段，不是每篇论文的默认步骤；
- 核心记录 usage 事实，但不计算价格和限制预算；
- 现有契约优先复用，通过适配迁移而不是从零重写。

### 12.2 仍待后续设计

- EvidenceBundle、DocumentPlan 和 ReviewPlan 的精确字段；
- ReviewPlan 是单文件契约还是现有布局/文本契约之上的哈希信封；
- 科研角色集合的最终兼容策略；
- `suspicious` 是否允许默认写出 Markdown，以及用户确认流程；
- 视觉问题的标准问题类型和回答 schema；
- PaperRecipe 的查询原语、循环上限和操作守恒规则；
- 旧 direct manifest 的弃用周期；
- usage 记录从 `llm_cost` 命名迁移到中性 usage 契约的兼容方式。

这些待设计项必须在各自实现前形成小型 ADR 或契约评审，不得通过桥脚本的临时字段
事实上定义长期协议。
