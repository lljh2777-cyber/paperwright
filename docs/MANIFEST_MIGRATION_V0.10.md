# manifest v0.10 文本复核派生包迁移说明

manifest v0.10 只由 `text-package` 写出。`layout-apply` 仍生成 manifest v0.9；
这样视觉布局结果保持为不可变父包，文本整理成为可审计、可丢弃的完整派生包。

## 新增内容

派生包在保留父包输出文件和视觉资产的基础上，重新生成：

```text
article.md
_paper2md/article-model.json
_paper2md/reader.json
_paper2md/manifest.json
_paper2md/06-text-review/text-task.json
_paper2md/06-text-review/text-review.json
_paper2md/06-text-review/validation-report.json
_paper2md/06-text-review/validation-report.md
```

manifest 的 `text_review` 摘要绑定父 manifest、源 Article Model、Text Task、
Text Review 与 JSON 验证报告的 SHA-256，并记录 reviewer 和操作数。完整输出清单
继续绑定每个交付文件的路径、大小与哈希。

## 迁移命令

```bash
paper2md text-package SOURCE_V09_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_V10_PACKAGE
paper2md validate-text-package OUTPUT_V10_PACKAGE
```

目标目录必须不存在。Paper2MD 先验证父包、task 和 review，在同级临时目录复制
证据、重新投影文章与 Reader、构造新 manifest，并完成一次全包校验；全部成功后
才原子发布目标目录。任何失败都不会修改父包或留下可误用的目标包。

## 兼容性

- direct/off 与 region-render 仍分别写 manifest v0.4 和 v0.5；
- `layout-apply` 写 manifest v0.9，并继续读取旧混合布局 v0.6–v0.8；
- `text-package` 首版只接受完整 manifest v0.9 父包，写 manifest v0.10；
- v0.10 不改变 Article Model v0.1、Reader v0.1、Text Task v0.1 或 Text Review
  v0.1 的内部契约。
