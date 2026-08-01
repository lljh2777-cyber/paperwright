# manifest v0.8 与 Reader v0.1 迁移说明

Paper2MD `0.8.0a0` 的混合布局写出从 manifest v0.7 升级到 v0.8。直接转换仍按
配置写出 v0.4/v0.5；旧混合布局 v0.6/v0.7 仍可由契约校验器读取，但不会被
原地改写。

## 新增文件和公共锚点

所有 `layout-apply` 证据级别现在都会生成：

```text
output-dir/
├── article.md
├── images/
└── _paper2md/
    ├── manifest.json
    └── reader.json
```

`reader.json` 是功能索引，不是可裁剪的调试证据。`article.md` 中原有的 page、
layout-region、caption-for 和 continuation 私有注释不再公开；每个阅读块前改为
以下稳定隐藏锚点之一：

```html
<!-- p2md:block id="blk_0123456789abcdef01234567" kind="body" -->
<!-- p2md:slot id="slot_0123456789abcdef01234567" asset="ast_0123456789abcdef01234567" -->
```

锚点格式版本为 `paper2md-markdown-anchor-v0.1`。消费端不得从数组下标、Markdown
行号、标题 slug、图片文件名或图注文本派生身份。v0.1 中每个锚点紧邻其后的
单行 Markdown block；段落内容不会跨物理行写出。

## manifest v0.8

v0.8 新增必需的 `reader` 摘要：

```json
{
  "contract_version": "paper2md-reader-v0.1",
  "path": "_paper2md/reader.json",
  "sha256": "<reader.json sha256>",
  "article_path": "article.md",
  "article_sha256": "<article.md sha256>",
  "anchor_contract": "paper2md-markdown-anchor-v0.1"
}
```

摘要中的两个路径和哈希必须与 `outputs` 中角色分别为 `reader_index` 和
`markdown` 的记录一致。路径必须是包内 POSIX 相对路径。

## Reader v0.1

Reader 顶层包含：

- `article`：正文路径、哈希、锚点和文本指纹契约；
- `capabilities`：当前可保证与不可保证的语义；
- `blocks`：按文章顺序排列的正文块和视觉槽位；
- `assets`：图片文件、尺寸、哈希、放置块和图注块；
- `relations`：v0.1 只允许 `places` 与 `caption-of` 边；正文 `mentions` 关系留给
  后续契约版本。

ID 从源 PDF SHA-256 和规范化源 span 确定性生成。图注只存在于对应 Markdown
block 中，asset 通过 `caption_block_id` 引用它，不复制图注全文。布局 provenance
v0.5 同时记录 block/asset 反向引用，便于审计，但 Reader 不应依赖 provenance
才能完成正常阅读。

相同源 PDF、相同最终布局角色和相同规范化 source spans 会得到相同 ID；如果重新
复核改变了 region/paragraph 边界、角色或对象归属，ID 会有意改变。消费端不应把
不同布局版本之间的 ID 相同视为无条件保证。

v0.1 的能力声明固定为：

```json
{
  "layout_semantics": "reviewed",
  "caption_binding": "reviewed-layout-geometry",
  "body_references": "unavailable"
}
```

因此 Reader 可以可靠展示 Figure 与图注、定位源页区域，但不能声称已识别正文中
的 `Fig. N` 引用。

## 消费端迁移步骤

1. 读取 `_paper2md/manifest.json`，按版本选择路径。
2. v0.8 先校验 Reader 和 article 的清单哈希，再读取 Reader。
3. 校验 `article.sha256`、所有 asset 哈希以及 Markdown 锚点集合。
4. 以 block ID 建立正文 DOM 映射，以 asset ID 建立 Figure 面板映射。
5. 根据显式 `caption-of` 关系显示图注；缺失关系时降级为无图注，不做文本猜测。
6. 对 v0.6/v0.7 包继续使用旧读取路径，或重新执行 `layout-apply` 生成 v0.8 包。

命令行可以直接验证完整包：

```bash
paper2md validate-reader output-dir/_paper2md/reader.json
```

## 人工编辑后的处理

编辑 `article.md` 会使严格哈希校验失败，这是预期行为。每个 block 保存规范化可见
文本的 SHA-256、64 位 SimHash 和长度，可供 Reader 发起显式重定位。指纹只能作为
候选证据：若无法唯一匹配，必须提示用户重新绑定或重新转换，不得自动篡改 ID 或
忽略哈希失败。
