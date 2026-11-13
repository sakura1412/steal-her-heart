---
name: steal-her-heart-pdf
description: 从已核验的竞品研究台账生成、渲染和检查 PDF 报告。
---

# 内置 PDF 报告导出

Reporter 在默认 PDF 交付时读取本页。从当前锁定的台账和审核过的稿件准备章节。简单版优先把结论与条件放在前面，详细版按本次决策组织证据、差异、限制和建议。按[内置导出器](../../references/report-export.md)生成真实、可搜索的 PDF。选择能覆盖正文字符的 TrueType 字体，导出器嵌入使用的字形，长段按页面宽度折行、续页。

按 PDF 实际页序渲染，例如执行 `pdftoppm -scale-to 1200 -png report.pdf preview/page`（先建 preview 目录）。逐页检查中文、换行、来源、数值单位和阅读顺序；需要横向矩阵或图形时，可用同一 ReportLab 库按稿件绘制表格或图，并按最终文件重新登记文本映射。通过[交付前校验](../../references/research-ledger.md#交付前校验)再交付。
