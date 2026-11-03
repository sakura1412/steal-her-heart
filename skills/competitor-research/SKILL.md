---
name: competitor-research
description: 在本竞品研究仓库引导创建或续建全行业项目，分批访谈并保存背景，先确认推荐维度再联网发现并确认四类候选。用于项目初始化，不用于修改Python框架或宣称已完成竞品报告。
---

# 竞品研究项目初始化

先读取仓库 [AGENTS.md](../../AGENTS.md)。用户请求创建项目、`/init-project 项目名` 或等价自然语言时，读取并执行 [初始化指令](../../commands/init-project.md)。以该指令为本入口的流程源；每轮只问2～3题，等待本轮答案，答不上来提供建议供用户选择。

使用仓库 [profile 模板](../../templates/profile.md) 和 [维度库](../../library/dimensions.md)，不限行业或读者。先补齐真实背景、保存profile，再生成并确认维度；确认后才用当前宿主真实联网工具找四类候选并请用户确认。尚无具体名称时先问，不把 `$ARGUMENTS` 当成项目名，不创建虚构业务项目。

工作发生在 `projects/<实际项目名>/`，复用已有内容及有效确认。候选事实逐项附本次来源链接和获取日期；用户自述、官方宣称、第三方和推断分开。维度与名单没有都被明确确认时保持等待，不能开始大规模采集。

本 Skill 是 Codex 聊天指令，不是已经接通的 Python 研究引擎。Python CLI 仍为单题访谈，`/analyze` 仍返回 pending_module；联网工具不可用时停在发现步骤并说明。只在初始化完成后提示下一步 `/analyze`，本次不执行分析、生成报告或伪造正式锁文件。

本目录当前存于 `skills/competitor-research/`，可以显式要求 Codex 读取本文件。没有仅凭该路径注册宿主 `/init-project` 或自动展开参数。仓库技能发现／显式调用规则见 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。
