---
description: 输入产品名称，通过 Steal Her Heart 技能及后续问答开展竞品研究。
argument-hint: 竞品名
---

# /analyze 竞品名（仓库兼容入口）

研究流程以 [Steal Her Heart 安装包](../codex-skills/steal-her-heart/SKILL.md) 为准，读取该文件执行；本文件不另存一套研究规则。

安装后在 Codex 直接输入 `$steal-her-heart 产品名`，或选择技能后回答产品名。不需要用户手动读取本文件、克隆仓库、记忆 `/analyze` 或创建我方项目。名称已给出则复用，按需进行简短问答；默认或直接开始即可继续。

Python CLI 的 `/analyze` 保留现有语法，只解析并返回 pending_module；不能用它代替 Codex 实际联网取证。用户主动选择仓库项目管理时，另用 [可选初始化入口](init-project.md)，不把它作为单对象分析的前置条件。
