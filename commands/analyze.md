---
description: 开发预览入口：Python研究链未就绪；按产品名称进入Codex宿主辅助研究，须实际联网核查，不保证报告。
argument-hint: 竞品名
---

# /analyze 竞品名（仓库兼容入口）

**当前 Python 仅支持初始化与工程验证，研究执行链路未就绪。** 这里的 Codex 宿主辅助研究与终端 Python `/analyze` 是两条路径，不会自动同步任务、维度或锁。

研究流程以 [Steal Her Heart 安装包](../codex-skills/steal-her-heart/SKILL.md) 为准，读取该文件执行；本文件不另存一套研究规则。

安装后在 Codex 直接输入 `$steal-her-heart 产品名`，或选择技能后回答产品名。不需要用户手动读取本文件、克隆仓库、记忆 `/analyze` 或创建我方项目。名称已给出则复用，按需进行简短问答；默认或直接开始即可继续。

Python CLI 的 `/analyze` 保留现有语法，只解析并返回 pending_module，并提供缺失能力及下一步指引；重试或重新初始化不会补齐执行链。要现在辅助查证，可采用 Codex 技能路径并逐条核查来源；没有宿主联网能力时说明缺口，不交付虚构报告。用户主动选择仓库项目管理时，另用 [可选初始化入口](init-project.md)，不把它作为单对象分析的前置条件。
