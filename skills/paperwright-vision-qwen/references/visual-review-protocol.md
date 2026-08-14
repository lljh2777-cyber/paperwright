# qwen-mm-plugins 视觉复核协议

模型无关的契约在前，qwen 只是具体实现。本文件给出每个复核环节的
**qwen 工具调用方式**和**契约映射**。所有结构化输出都必须通过
PaperWright 校验器后才能使用。

## 工具速查

来自 qwen-mm-plugins（MCP）：

- `vision_chat(images=[路径], text=提示)` — 看图回答，主工具；
- `grounding(image_path, prompt)` — 检测/定位对象，返回归一化 bbox；
- `ocr(image_path, prompt)` — 提取图中文字；
- `read_image` / `crop` / `draw_bbox`（core）— 读图、切块、标注。

模型选择：`vision_chat` 使用当前账号可用的 VL 模型；若该模型在
`layout-prepare`/`layout-apply` 的验证链上报告不可用，按
SKILL.md 的 Troubleshooting 处理，不硬编码模型名。

## 1. 探测

开始前先确认工具可用：

```text
vision_chat(images=[<某页 page.png>], text="Describe this page briefly.")
```

成功才继续；失败则整体退回人工复核路径。

## 2. Content ROI 建议

目标：确认页面有效内容的大致矩形（标题、作者、脚注、图/表、caption 都
必须在内），用于 `layout-prepare` 的 ROI 确认。

```text
vision_chat(images=[page.png], text=
  "This is page N of a scientific paper. Ignore running headers, footers and
   page numbers. Return the bounding box (as fractions of the page, x y w h)
   of the smallest rectangle that contains ALL main content: title, authors,
   body, figures, tables and captions. JSON: {\"x\":..,\"y\":..,\"w\":..,\"h\":..}")
```

映射到 `content-roi.json`（现有 schema）；**人工确认步骤不可跳过** ——
模型建议只是提案。ROI 过窄或漏了标题/图注时，修正后重新确认。

## 3. visual-direct 区域（final-layout.json）

目标：给每页划区域并标注角色与阅读顺序。

```text
vision_chat(images=[page.png], text=
  "For each semantic region on this page, output JSON array:
   [{\"role\": \"body|heading|caption|figure|table|unknown\",
     \"bbox\": {\"x\":..,\"y\":..,\"w\":..,\"h\":..},
     \"reading_order\": <int>}].
   Include the page furniture (running header, footer, page number) as role
   \"excluded\" if present. Fractional coordinates, no transcription.")
```

角色边界（与 PaperWright 一致）：
- `body` 正文（含跨列续行）；`heading` 标题/小节；`caption` 图注；
- `figure`/`table` 视觉区；页眉/页脚/页码 → `excluded`（会被剔除）；
- 不确定的给 `unknown`，**不要猜**。

映射到 `final-layout.json`，`source_element_ids` 一律留空数组 —— PaperWright
回接原始 PDF 元素，模型不发明 ID。

## 4. join-blocks 断句确认

目标：判断两块文本是否同一段。这是文本证据（小写续行 + 前块无句末标点）的
**视觉佐证**，两者一致才构造操作。

```text
vision_chat(images=[page.png], text=
  "Fragment A (bottom-left column) ends with '...tumor-promoting inflammation'.
   Fragment B (top-right column) begins with 'and immune evasion...'.
   Are A and B the same paragraph? Answer exactly:
   SAME_PARAGRAPH or DIFFERENT_PARAGRAPHS
   Reason: <one sentence citing text flow, indentation, or column position>")
```

判定规则：
- 视觉说 `SAME_PARAGRAPH` **且**文本证据满足（同页、order 相邻、body 类型、
  A 不以 `.?!:;` 结尾、B 首字符小写）→ 构造 `join-blocks`；
- 任一不满足 → 不构造。视觉不能推翻校验器；校验器不能代替视觉判断版面。
- 视觉说 `DIFFERENT_PARAGRAPHS` 而文本证据弱 → 不拼（保守）。

## 5. 图注/图片核查（可选）

```text
ocr(image_path=figure.png)  → 图中文字
vision_chat(images=[figure.png, page.png], text=
  "Does the caption below match this figure's content? yes/no + one-line why.")
```

发现问题 → 报告为复核提示，不自动改布局；由主 Agent 决定是否回到
visual-direct 复核。

## 6. 校验与失败处理

- 每个 JSON 产物过对应校验器（见 SKILL.md）；
- 校验失败 → 修正产物或退回人工，**不弱化契约**；
- 模型不确定 / 图片模糊 / 工具 403 → 明确报告，退回人工复核路径；
- 该 skill 不可用时，`paperwright-convert` / `paperwright-agent-workflow`
  的人眼复核流程完整可用，不受影响。
