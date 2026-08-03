# manifest v0.9 与 Article Model v0.1 迁移说明

## 变更目的

manifest v0.9 为混合布局文档包增加规范文章模型：

```text
_paper2md/article-model.json
```

此前 `article.md` 与 `reader.json` 由同一次内存编译生成，但没有可持久化、可验证
的共同来源。v0.9 将复核后的文章块、稳定 ID、source spans、视觉资产和关系保存为
`paper2md-article-model-v0.1`，然后从该模型确定性生成 Markdown 与 Reader。

## manifest 新字段

v0.9 在保留 v0.8 `reader` 摘要的同时，新增：

```json
{
  "article_model": {
    "contract_version": "paper2md-article-model-v0.1",
    "path": "_paper2md/article-model.json",
    "sha256": "<article-model.json sha256>"
  }
}
```

`outputs` 中必须存在相同路径、哈希和 `article_model` role。Article Model 与
Reader 都属于功能索引，因此在 `minimal`、`standard` 和 `full` 证据级别中均会
保留。

## Article Model v0.1

顶层字段为：

- `contract_version`；
- `source_sha256`；
- 顺序稳定的 `blocks`；
- `assets`；
- `relations`。

每个 block 保存稳定 ID、语义 kind、连续 order、单行 Markdown、source spans 和
可选视觉资产 ID。锚点、可见文字指纹与 article 哈希属于确定性投影，不在模型中
重复保存。

## 兼容性

- 当前写出 manifest v0.9；
- 继续读取 manifest v0.6、v0.7 和 v0.8；
- v0.8 文档包没有 Article Model，不会被静默伪造或升级；
- `article.md`、Reader v0.1 和 Markdown anchor v0.1 的公开格式保持不变。

## 验证

完整验证 v0.9 文档包的共同来源与两个投影：

```bash
paper2md validate-article-model output-dir/_paper2md/article-model.json
```

该命令会检查 Article Model 契约、稳定身份、关系、图片资产，并验证磁盘上的
`article.md` 和 `_paper2md/reader.json` 是否与模型的确定性投影完全一致。

旧版 Reader 验证入口继续可用：

```bash
paper2md validate-reader output-dir/_paper2md/reader.json
```
