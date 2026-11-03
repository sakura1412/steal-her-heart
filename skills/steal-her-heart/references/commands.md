# 命令和自然语言入口

版本：2；2026-11-03。适用于 Steal Her Heart 项目，面向全部行业和系统品类。研究任务统一解析意图后先检查阶段0项目访谈，再由主控执行。初始化是独立前置入口，不新增 research request 的意图枚举。

| 命令 | 等价自然语言示例 | 目标行为 |
|---|---|---|
| /init-project 项目名（聊天指令） | 帮我新建某产品的竞品研究项目 | 分批访谈并保存 profile，先确认维度，再联网发现和确认四类候选 |
| /analyze 竞品名 | 帮我分析 Cursor；更新海底捞的竞品档案 | 采集并更新单个竞品档案，保留来源、快照与历史版本 |
| /battlecard 竞品名 | 生成 Cursor 的销售一页纸；给 Claude Code 做一张 Battlecard | 使用有效研究资产和我方背景生成一页销售卡 |
| /brief 周报 | 生成面向四类角色的周报；生成竞品周报 | 按周报范围生成配置读者视图 |
| /brief 月报 | 帮我生成一份竞品月报 | 按月报范围生成配置读者视图 |

/brief 不带周期时询问周报还是月报，不自行猜测；也接受 weekly／monthly。周报和月报是用户即时请求，不自动创建定时任务。具体起止日期、档案集合和时区要在后续计划中明确，不能将近7天、自然周或自然月混用。

单个竞品名可以是任意行业的名称；不根据“销售卡”推断为某个垂直品类。包含空格的名称整体保留。实体是否同名／有歧义仍由理解与确认阶段解决，路由成功不代表已消歧。

## 聊天初始化入口

读取 [competitor-research 技能](../../competitor-research/SKILL.md) 与 [commands/init-project.md](../../../commands/init-project.md)，按实际项目名执行。每轮2～3题，用户答不上来时给建议选项；不把未选择的建议或 `$ARGUMENTS` 占位符当作答案。完成 profile 后推荐并预确认 dimensions.md，再联网搜索四类候选并等待名单确认，两项确认后提示下一步可请求 /analyze。

上述是可复用的聊天指令文件，尚未注册宿主 /init-project 快捷命令。Python 的 /init-project 是子命令别名，项目名仍通过全局 --project-name 指定，交互仍一次一题；不能把聊天文本直接当作现有 CLI 的位置参数格式。初始化预确认不等于最终 lock，兼容关系见 [运行流程](research-workflow.md)。

## CLI 入口

当前可用输入形式：

```bash
steal-her-heart request "/analyze Cursor"
steal-her-heart request "生成 Cursor 的销售一页纸"
steal-her-heart request "生成面向四类角色的周报"
steal-her-heart /analyze "任意品牌"
steal-her-heart /battlecard "Claude Code"
steal-her-heart /brief 月报
```

analyze、battlecard 也支持不带斜杠的 CLI 别名。旧 CLI 的 brief 指 ResearchBrief 识别，保留兼容；建议改用 recognize-brief。带斜杠 /brief 才是周报／月报任务，二者互不覆盖。

这些是项目内输入语法，没有安装或注册宿主应用的快捷命令。在 Codex 聊天中可让 Codex 读取本技能，再将用户原文交给 CLI request。不得假装已注册 Codex 内置命令，或绕过 Registry 直接拼接任意 shell。

## 已实现与尚未实现

[command_router.py](../../../src/steal_her_heart/command_router.py) 提供 CommandIntent、CommandResponse 和 RequestRoutingTool，经 Registry 校验输入、输出与 request_routing 能力。支持明确的常见中文句式及示例；不是无限制自然语言理解。未知、复合或不支持的请求返回问题，不执行第一条后丢弃剩余要求。

当前提供解析和初始化闸门：

- 已识别任务但阶段0未完成：execution=initialization_required，返回下一访谈问题，退出3。
- 已初始化且任务完整：intent.status=parsed，execution=pending_module，退出3。
- 缺竞品或周期：needs_clarification，退出3。
- 不支持的意图或非法输入：退出2。
- 工具故障：退出1。

输出统一 ToolResult 包装；output_path 始终为空，不创建数据库、不更新竞品档案、不生成卡片或报告。解析成功绝不能标为采集／生成成功，不能伪造任务完成状态。

## 后续执行与验收

/analyze 进入理解、实体确认、计划及范围内取证流程，资料版本更新不能覆盖旧证据。/battlecard 优先复用仍有效资产；缺资料补证，缺我方背景只出缺口草稿。/brief 对同一期间研究资产按配置组织视图，不补造历史事件；无历史快照不能推断趋势变化。

上述执行依赖尚未实现的 Planner、研究执行、资产查询与 Reporter，按阶段接入；不因新增命令跳状态、扩大权限或新增确认节点。聊天先维度预确认、再候选确认；现有 Python 最终提案与锁仍要求名单批准在先。没有预确认迁移桥接时报告未接入，不能以初始化已结束为由跳过 lock；范围改变重走相应评审。

本次验收：命令和自然语言归一、任意品类名称、配置读者与周期、缺参数、未知命令、复杂请求拒绝、Registry 权限及 Schema、CLI 无副作用、旧 brief 兼容。真实档案更新、一页渲染与报告生成要在后续分别验收。

角色来自项目或阶段2锁定配置；旧四类只是预设。阶段0只有原始读者文本时，路由保留 reader_requirements，audiences 为空，不自动补四类。详见 [阶段0–6运行规范](research-workflow.md)。
