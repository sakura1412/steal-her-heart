---
name: steal-her-heart-pptx
description: 从已核验的竞品研究台账生成和检查可编辑的 PowerPoint 汇报。
---

# 内置 PowerPoint 报告导出

用户要求 PPT 时，Reporter 从 PDF 所依据的同一台账快照独立组织幻灯片。每张围绕一个决策问题，标题点明场景，正文包含结论、条件和限制。可为 PPT 单独准备更简短的导出计划，保留对应的 Claim 版本。按[内置导出器](../../references/report-export.md)生成 16:9 的 .pptx，文字框可在 PowerPoint 编辑。收件端需要适用字体。长内容可分成多页；需要原生图表或表格时，可用同一 python-pptx 库创建并重新登记最终文本映射。

可用本地 LibreOffice 将 PPTX 渲染为检查用 PDF，例如 `soffice --headless --convert-to pdf --outdir preview report.pptx`。逐页查看渲染结果，检查中文字体替换、文本溢出、缺失对象和关键条件。若同时交付 PDF，核对两份文件中的数字、限制和建议。通过[交付前校验](../../references/research-ledger.md#交付前校验)后交付原始 .pptx。

若预览只显示数字或英文，先确认渲染器能找到导出时选用的中文字体。使用 Fontconfig 的环境可将 FONTCONFIG_FILE 指向含该字体目录和可写缓存目录的配置，再重新渲染各页。
