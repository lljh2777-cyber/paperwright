# Phase 4 region-render spike 产物索引

- `frozen_annotations_v1.json` / `frozen_annotations_zh.md`：pre-render 标注；
- `baseline_source_hashes.json`：权威 base 的源码哈希；
- `bbox_machine_stats.json`：bbox、像素、面积、caption overlap 与哈希；
- `spike_summary.json`：8 篇回归、双轮目标与 18 项门禁；
- `visual_review.json` / `manual_visual_review_zh.md`：三目标人工视觉证据；
- `change_notes_zh.md`：实现与 pre-fix→final；
- `test_summary.json` / `test_report_zh.md` / `test_evidence/`：测试；
- `REPRODUCE.md`：可直接执行的复现命令；
- `report_zh.md`：总报告。

真实 PDF、完整输出、裁剪 PNG、全页渲染和接触图均只留在云端 runtime，
不进入源码树交付包。
