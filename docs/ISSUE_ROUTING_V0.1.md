# Issue-level Routing v0.1

## 1. 目的

`paperwright-issue-routing-v0.1` 把路由对象从“整页”改成“有证据、可定位的未决问题”。
每个源页面始终先由 L0 确定性处理；L1/L2/L3/人工只接收具体 issue，不再把一页中
已经能由规则处理的正文一并升级。

准备目录同时保留旧 `routing.json` 作为兼容产物，新的语义真值是
`issue-routing.json`。

## 2. 契约

顶层字段包括：

- `contract_version`: `paperwright-issue-routing-v0.1`
- `mode`: `issue-first`
- `source_sha256` 与 `page_count`
- `pages`: 每页固定 `base_route: L0_RULE`，并列出该页 issue
- `issues`: 局部问题、处理层、降级层、证据、范围和状态
- `summary`: 按 route/kind 的精确计数

每个 issue 的 `scope` 可绑定归一化 bbox、PhysicalDocument element ID、布局 candidate
ID，或后布局阶段的文本 block pair。跨页 issue 仍以一个页面作为 `page_index`，并用
`scope.related_page_indices` 声明其余参与页面；兼容页级视图会把同一个 issue 投影到
所有参与页面。Python 校验器会检查页/issue 引用守恒、摘要计数、
route 优先级、坐标合法性和文档身份；JSON Schema 位于
`src/paperwright/schemas/issue_routing.schema.json`。

## 3. 两阶段发现

### 3.1 布局前

`layout-prepare` 只使用 PhysicalDocument、LayoutTask、LayoutRiskAssessment 和栅格证据，
生成以下问题：

- `caption_visual_binding`: 明确 Figure caption 与同页视觉证据需要语义绑定，走 L2；
- `cross_page_caption_visual_binding`: 后一页顶部 Figure caption 与前一页底部视觉候选
  需要成对页面判断，issue 锚定 caption 页并把前一页列入 `related_page_indices`，走 L2；
- `layout_geometry_ambiguity`: 复杂候选或硬风险，走 L2；
- `page_visual_preservation`: 无文字但非空的页面，走 L0 整页视觉保留；
- `native_text_evidence_missing`: 既没有文字也没有可证明空白/非空的证据，走人工。

Table 仍优先进入确定性的同页表格检测/渲染，不因仅有 Table caption 就调用视觉模型。

### 3.2 布局后

段落边界在 ArticleModel 形成前并不可靠。因此编排器先运行 `layout-apply`，再从
`text-prepare` 产物中调用与 `validate-text-review` 相同的 `join_candidates` 前置条件，
生成精确 `paragraph_continuation` issue。每个 issue 绑定恰好两个相邻 block ID 和当前
block 的源 bbox，走 L1，失败时降级 L3。

Completeness Gate 的孤立 caption、漏投影和整页渲染失败也会回流为局部 issue。布局后
新增项写到输出目录旁的 `<output>.resolve-issues.json`，不修改已经发布的包，也不覆盖
已有 resolution plan。

## 4. 执行适配

`tools/run_routing_plan.py` 优先读取 `issue-routing.json`：

1. 所有无视觉/人工 issue 的页面先生成 L0 layout；
2. L2 桥只收到对应页的局部 issue 原因、信号和 scope；
3. `layout-apply` 后发现精确 L1 block pair；
4. L1 桥只发送命中这些 pair 的短文本片段；
5. Completeness finding 写入 resolution plan，供下一次局部 resolve。

L2 已优先使用候选关系协议：issue scope 中缺失于普通候选的 Figure caption 会成为只读
anchor candidate，模型输出分组/角色/caption-of，程序确定性编译 `final-layout.json`。
只有完全没有候选时才回退旧整页画框兼容协议。详见
[VISUAL_RELATIONS_V0.1](VISUAL_RELATIONS_V0.1.md)。

跨页 caption issue 会先让两页分别完成 FinalLayout，再生成只含稳定 region ref 的成对
关系任务。模型只能从前页已有 visual 中选择一个绑定，或显式拒绝；接受结果和拒绝结果
都参与确定性回放。详见 [CROSS_PAGE_CAPTION_V0.1](CROSS_PAGE_CAPTION_V0.1.md)。

真实 seed 校准后，跨页召回还识别三类强方向证据：前页显式写明 caption/legend 在下一
页、后页 caption 邻近 `◀`/previous-page 标记、以及前页原生文字极少但 raster evidence
非空。由此可覆盖 caption 位于后一页底部和标签被拆成独立元素的论文，同时用同页视觉
困难负例抑制仅凭“caption 靠页顶”的误升级。

路由只依据内容证据、结构风险和验证结果，不使用模型价格、token 预算或供应商身份。

## 5. A06 真实论文回归

对 32 页论文 *Genomic landscape of the human vaginal microbiome...* 使用 standard
选择性取证：

- 32/32 页 `base_route` 均为 L0；
- 6 个 Figure-caption binding issue：第 3、4、5、8、9、10 页；
- 1 个复杂几何 issue：第 28 页；
- 第 30–32 页为 L0 整页视觉保留，不再误路由到人工；
- 布局前不生成猜测性 L1 issue；
- 总计 10 个 issue，22 页无需升级，视觉桥兼容适配覆盖 7 页。

真实论文及渲染结果保存在仓库外，仓库只提交契约、代码、测试与聚合结论。
