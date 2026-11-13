# 使用包内导出器

[PDF 模块](../modules/pdf/SKILL.md)与[PPT 模块](../modules/pptx/SKILL.md)都随主技能安装。Agent 以技能目录为工作目录，使用具备 [requirements.txt](../scripts/requirements.txt) 所列库的 Python 3.9+ 环境；必要时在可用虚拟环境运行 `python -m pip install -r scripts/requirements.txt`。字体取本机可用于报告的 TrueType 字体。渲染可使用宿主提供的普通 PDF 或 Office 工具。

## 导出计划

对要交付的每种格式，把内容 QA 后的稿件写为一个 UTF-8 JSON 文件。input_manifest_sha256 取锁定的 quick.json 或 manifest.json 的实际字节哈希。sections 保持演示顺序，每节含标题与若干内容块。以下是字段示例，实际使用时替换快照哈希与 Claim 引用。

```json
{
  "input_manifest_sha256": "<快照文件的 SHA-256>",
  "sections": [{
    "title": "首周租赁成本",
    "blocks": [
      {"kind": "analysis", "claim_refs": [{"record_id": "break-even", "revision": 1}]},
      {"kind": "limitation", "text": "本次报价仅覆盖首周。"}
    ]
  }]
}
```

fact 与 analysis 块须绑定已支持的 Claim 和已核验证据；默认使用台账原文。可用 text 指定已通过内容 QA 的报告措辞，供不同受众或详略使用。limitation 记录缺口。默认附证据编号、来源及获取时间；用户明确要求隐藏来源的展示副本时可设置 presentation_copy 为 true，内部映射仍保留。受限或不足快照会在正文开头展示台账缺口。章节布局支持标题、分段文字、来源附录与自动分页。

## 生成与验收

```sh
python scripts/export_report.py "$RUN_DIR" "$PDF_PLAN" reports/report.pdf --font "$CJK_FONT"
python scripts/export_report.py "$RUN_DIR" "$PPT_PLAN" reports/report.pptx --font "$CJK_FONT"
```

只执行用户需要的格式。复用原件时先比对输入快照；重新导出使用新文件名。脚本拒绝覆盖已有文件，并在产物旁保存 .draft.json，包含产物哈希、快照绑定、逐行 Claim 引用和 1.2.0 段落映射。生成时回读可提取文字，检查与排版内容一致。

打开最终文件，完成内容、渲染、引用及适用的跨格式核对。将草稿记录中实际通过的 checks 更新为 passed，单格式的 cross_format_consistency 可记 not_applicable；通过后设置 status 为 qa_passed，将该条 JSON 记录按单行追加到 reports/reports.jsonl。对每件交付物执行 [validate_run.py](../scripts/validate_run.py) 的 --artifact 检查。有排版修改时重新核验文件哈希、文本范围和渲染结果。依赖或渲染工具不可用时按[交付规则](report-delivery.md)报告实际状态。
