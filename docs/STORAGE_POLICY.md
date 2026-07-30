# 仓库存储政策

允许提交：

- Python 源码、JSON Schema、配置；
- 小型自生成 fixture；
- 单元测试；
- 文档、许可证/哈希清单和小型机器摘要。

禁止提交：

- 论文 PDF、论文提取图像和真实转换输出；
- PDFium、JAR、动态库、可执行文件或 wheel；
- corpus、gold payload、大型 render；
- 虚拟环境、缓存、node_modules；
- `.env`、令牌、私钥、cookie、凭据；
- 单文件超过 5 MiB 的 Stage A/Stage B 源码交付内容。

`tools/check_repo_policy.py` 对候选工作树执行扩展名、大小、名称和内容模式
检查。它是防误提交措施，不替代正式秘密扫描或发布级供应链审查。
