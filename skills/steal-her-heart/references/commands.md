# 命令和自然语言入口

Codex 用户默认只需安装 [Steal Her Heart](../../../codex-skills/steal-her-heart/SKILL.md) 一次，之后使用 `$steal-her-heart 产品名` 或选择技能后提供名称，后续按需问答。无需手动读取本页或运行 Python。本页以下 `/analyze` 等是仓库兼容入口及 Python 语法，不表示自动注册了同名宿主斜杠命令。

版本：3；2026-11-03。默认只需研究对象，不先检查我方背景或项目访谈。显式项目模式的文件与快照校验单独保留。

| 命令 | 等价自然语言示例 | 输入要求与目标 |
|---|---|---|
| /analyze 竞品名 | 帮我分析 Notion；分析海底捞 | 名称即可开始单对象研究，背景可选 |
| /battlecard 竞品名 | 给某品牌做销售一页纸 | 先复用竞品画像；我方回应和优势需相关我方背景 |
| /brief 周报／月报 | 帮我生成竞品月报 | 周期与研究资产范围；不要求我方产品 |
| /init-project 项目名（可选聊天指令） | 为长期研究建立项目配置 | 主动选择的项目模式，保存背景、固定维度与读者 |

## 默认分析

Codex 读取 [competitor-research 技能](../../competitor-research/SKILL.md) 与 [commands/analyze.md](../../../commands/analyze.md)，可以根据上下文把裸名称理解为单对象请求。仅对实体歧义提问；明确对象后选择合适维度、取证、核查和分析，不先要求项目名、我方产品、市场、目的或读者。

用户点名的对象限定本次范围。自动设计常规维度不设等待确认节点，不自动扩大成其他竞品的采集任务；实质扩范围再询问。用户附带关注点直接使用，来源的日期、地区与版本条件仍逐条保留。

Python 当前只解析明确命令与已支持的自然语言句式，`request "Notion"` 不会被猜为分析；使用 `analyze Notion`、`/analyze Notion` 或 `request "分析 Notion"`。名称中空格整体保留；实体核验仍由后续理解／实际研究负责。

## Python CLI

```bash
steal-her-heart /analyze Notion
steal-her-heart request "帮我分析海底捞"
steal-her-heart /battlecard "任意品牌"
steal-her-heart /brief 月报
```

默认不读 projects/default，不创建我方业务项目。`run` 使用本地 `.steal-her-heart/memory.sqlite3` 或 `--db`，可保存 project_context=None；后续执行仍未接入。显式指定 `--project-name` 或 `--project` 时才加载项目，文件缺失／未完成／格式错误按项目模式处理，不能悄悄降级丢弃背景。

`/brief` 不带周期时询问周报或月报，也接受 weekly／monthly。确切起止日期、时区和档案集合在未来执行前明确，不能默认把自然周、近七天混用。请求简报不授权定时任务。

`recognize-brief` 是独立的离线需求识别器；旧 `brief` 是其兼容别名。带斜杠 `/brief` 才表示周期报告，二者互不覆盖。项目 CLI 语法不是已注册的 Codex 宿主快捷命令。

## 当前返回值与能力边界

[command_router.py](../../../src/steal_her_heart/command_router.py) 通过 Registry 校验请求与结果：

- 无项目且请求完整：execution=pending_module，退出3，不要求先初始化。
- 缺对象或周期：needs_clarification，退出3；未知／不支持的复杂请求不静默猜测。
- 显式选择的项目未完成：initialization_required，带该项目的补全提示。
- 非法输入或不支持意图：退出2；工具故障：退出1。

当前 `/battlecard` 与 `/brief` 的完整意图也返回 pending_module；提醒后续执行所需的我方信息或资产范围，不把路由伪装成完成生成，也不为了缺少尚无提交接口的字段建立无法回答的循环。Codex 自由聊天可按需补问最少信息；不必完成十一题项目问卷。

所有路由 output_path 为空，不采集、不更新档案、不生成报告；实际搜索、实质核查、Analysis 和 Reporter 尚未接入 Python。Codex 宿主按指令真实执行的研究须另有来源及产物证据，不能从解析成功推导研究完成。

## 可选项目与读者

用户选择长期跟踪、我方对照或固定读者时，可按 [init-project.md](../../../commands/init-project.md) 分批初始化，Python 仍逐题保存。此模式的名单／维度评审和范围锁保持原有约束，详见 [运行流程](research-workflow.md)。默认单对象入口不要求伪造这些批准。

未配置读者时输出通用概览；有配置时复用 ReaderProfile，不自动填四类角色。无我方产品时不生成我方优势、反驳或赢输结论；单对象事实画像可正常研究。多角色输出共用同一事实版本。
