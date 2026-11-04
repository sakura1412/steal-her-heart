# 命令和自然语言入口

**开发预览：Python当前仅支持初始化与工程验证，研究执行链路未就绪。** Codex用户可安装 [Steal Her Heart](../../../codex-skills/steal-her-heart/SKILL.md)，用 `$steal-her-heart 产品名` 进入宿主辅助研究试验路径；先披露能力边界，再采用名称和按需问答，结果依实际工具与证据，不保证报告。无需手动读本页或运行Python，但宿主路径不自动同步Python任务、维度或最终锁。以下 `/analyze` 等是仓库兼容入口及Python语法，不表示自动注册同名宿主斜杠命令。

版本：8；2026-11-04。默认只需研究对象，不先检查我方背景或项目访谈。显式项目模式的文件与快照校验单独保留。Python提供独立工具诊断与pending的下一步指引，不等于研究命令已接通。

| 命令 | 等价自然语言示例 | 输入要求与目标 |
|---|---|---|
| /analyze 竞品名 | 帮我分析 Notion；分析海底捞 | 名称即可开始单对象研究，背景可选 |
| /battlecard 竞品名 | 给某品牌做销售一页纸 | 先复用竞品画像；我方回应和优势需相关我方背景 |
| /brief 周报／月报 | 帮我生成竞品月报 | 周期与研究资产范围；不要求我方产品 |
| /init-project 项目名（可选聊天指令） | 为长期研究建立项目配置 | 主动选择的项目模式，保存背景、固定维度与读者 |

## 即时简报与监测的目标语义（尚未迁移）

目标 `/brief` 为本次即时简报，可从当前范围取对象，内容窗口可选；“周报／月报”描述资料窗口，不创建周期任务。不指定窗口仍须披露资料原时点，不能声称已更新。持续监测另需明确对象、频率、时区、预算、通知及停止配置和授权，当前无调度器。详见 [产品规则](../../../docs/product-design.md)。

上表及下文 CLI 保留当前行为：Python `/brief` 仍要求周／月周期；裸 `brief` 仍为旧离线识别器，不因目标设计改义。新语义须独立迁移和测试，本文件不是新命令注册。

## 默认分析

Codex 读取 [competitor-research 技能](../../competitor-research/SKILL.md) 与 [commands/analyze.md](../../../commands/analyze.md)，可以根据上下文把裸名称理解为单对象请求。仅对实体歧义提问；明确对象后选择合适维度、取证、核查和分析，不先要求项目名、我方产品、市场、目的或读者。

用户点名的对象限定本次范围。自动设计常规维度不设等待确认节点，不自动扩大成其他竞品的采集任务；实质扩范围再询问。用户附带关注点直接使用，来源的日期、地区与版本条件仍逐条保留。

Python 当前只解析明确命令与已支持的自然语言句式，`request "Notion"` 不会被猜为分析；使用 `analyze Notion`、`/analyze Notion` 或 `request "分析 Notion"`。名称中空格整体保留；实体核验仍由后续理解／实际研究负责。

## Python CLI

`steal-her-heart --help` 和研究子命令 `--help` 先标明当前状态。完整请求仍返回pending_module，同时在guidance列出missing_modules、next_steps及适用时的codex_prompt；终端人类提示走stderr，stdout是一份JSON。单对象可用Codex宿主工具辅助查证；Battlecard需双方资料才能写我方回应；brief需明确周期起止日期、对象与来源材料，当前没有Python资产查询可以自动补齐。重试或重新初始化不能使链路就绪。

```bash
steal-her-heart /analyze Notion
steal-her-heart request "帮我分析海底捞"
steal-her-heart /battlecard "任意品牌"
steal-her-heart /brief 月报
```

默认不读 projects/default，不创建我方业务项目。`run` 使用本地 `.steal-her-heart/memory.sqlite3` 或 `--db`，可保存 project_context=None；后续执行仍未接入。显式指定 `--project-name` 或 `--project` 时才加载项目，文件缺失／未完成／格式错误按项目模式处理，不能悄悄降级丢弃背景。

`/brief` 不带周期时询问周报或月报，也接受 weekly／monthly。确切起止日期、时区和档案集合在未来执行前明确，不能默认把自然周、近七天混用。请求简报不授权定时任务。

`recognize-brief` 是独立的离线需求识别器；旧 `brief` 是其兼容别名。带斜杠 `/brief` 才表示周期报告，二者互不覆盖。项目 CLI 语法不是已注册的 Codex 宿主快捷命令。

### 独立公开搜索诊断

```bash
steal-her-heart search "任意产品 官方信息" --max-results 5
```

`search` 通过 Registry 调用 web_search，无需项目或我方背景；它不进入研究状态机。默认只请求一次 DuckDuckGo HTML 公开结果页，结果数1～10、默认5，输出包含标题、链接、摘要、获取日期与搜索响应散列。搜索命中未抓取核验，`evidence_eligible=false`，不得转写为 Evidence 或“查找后未找到”的字段结论。

stdout 仅为 JSON ToolResult；stderr 输出 `search_task_id=<UUID>` 和 `search_request_id=<UUID>`，失败时也保留两者。后续通过 `--task-id` 复用同任务额度，通过 `--request-id` 重放同一次查询的本地完成记录；未提供标识时由 CLI 先生成再传入工具。`--search-ledger <路径>` 指定默认 `.steal-her-heart/search-calls.sqlite3` 以外的账本。默认每任务最多30次，联网前持久预留，错误计数；相同任务、请求与参数的已完成结果可本地重放，不重复联网，未完成调用不自动重发。

成功退出0，执行／访问受限错误退出1，无效参数退出2。验证码和限流返回 SEARCH_ACCESS_RESTRICTED，绝不冒充空结果。此前真实探测遇到 HTTP 202 验证页后已停止，尚未验证真实命中路径；不换入口或自动重试。此处是 Python 子命令，没有注册宿主 `/search` 快捷命令。

### 独立静态页面与来源快照

```bash
steal-her-heart fetch "https://example.com/"
```

`fetch` 经Registry调用web_fetch，不读取项目、不进入状态机、不自动跟进搜索结果。只接受公开HTTP(S) URL和对应能力，获取静态HTML／XHTML／纯文本，响应最多1MiB、CLI请求超时15秒；不执行JS、不跟随跳转、不自动重试。DNS与连接目标须为公网，TLS验证保持开启；验证码、访问限制及代理认证挑战返回FETCH_ACCESS_RESTRICTED，失败不冒充“未找到”。

成功后保存原始bytes、规范正文及manifest，原文与正文散列、行数、解析器版本可重新核验；published_at未知保持null。stdout仅JSON ToolResult，包含document、最多12000字符text_preview、text_truncated及固定facts_validated=false。正文完整版本由snapshot_ref在本地store加载；抓取成功不产生Evidence、Validator PASS或报告。

stderr始终输出fetch_task_id和fetch_request_id，未指定时CLI先生成，失败也可取回。`--task-id`复用每任务60次抓取额度，`--request-id`重放同请求；`--fetch-ledger`默认 `.steal-her-heart/fetch-calls.sqlite3`，`--sources-root`默认 `.steal-her-heart/sources`。搜索与抓取有不同数据库标识，不得混用；联网前预留、错误计数、未完成调用不重发。成功重放也核验快照，缺失或篡改返回错误，不自动重新抓取。

成功退出0，参数错误2，其他执行错误1。离线测试和真实网页验收分别记录；这个Python子命令不注册宿主`/fetch`，也不接通`/analyze`或run。独立计数尚未成为跨工具、模型、轮次与活动时间的全局研究预算。

来源快照依赖POSIX目录能力，当前支持macOS／Linux；能力缺失时在预算预留和联网前返回FETCH_SNAPSHOT_UNAVAILABLE。此前对 `https://example.com/` 的单次CLI抓取、快照校验和同ID本地重放已实测，未再次请求受限搜索服务；这不等于任意网页、竞品证据或完整分析已验收。

### 显式候选摘录与定位

```bash
steal-her-heart extract-candidates "sha256:<manifest-digest>" \
  --text-sha256 "<text-sha256>" \
  --competitor "产品名" --dimension "研究维度" --span 0:20
```

用fetch输出的document.source.snapshot_ref和document.text_sha256替换占位符，在完整规范正文上明确选择范围。--span可重复，每次1～20段、每段最多4000字符；按Unicode码点从0计数，start包含、end不包含，输出行号从1开始。--sources-root默认.steal-her-heart/sources。工具extract_features通过Registry要求evidence_extraction能力，CLI超时5秒；快照不匹配、损坏、纯空白、重复或越界片段失败，不返回部分成功。

stdout仅JSON ToolResult，成功data.status=candidates_only、facts_validated=false，每个候选support_status=unvalidated，fact精确等于quote，带Source、确定性ID和版本／范围／行号locator。stderr输出extraction_task_id，成功退出0、参数错误2、其他执行错误1。输入合法性经CLI／Registry检查后才读快照；没有新增ledger或request_id，因为执行只读本地，不联网、不调用模型、不写任务或文件。

这不是自动语义提取，也没有检查对象相关性、主张支持或时效；不构造既有Evidence、PASS或报告。原fetched_at保留，不把读取旧快照说成本次新检索、有效性确认或跨任务增量研究。同任务ledger重放、Memory revision和状态events也不形成研究资产版本历史；资产目录、时效核验与更新链仍待Phase 6。

## 当前返回值与能力边界

[command_router.py](../../../src/steal_her_heart/command_router.py) 通过 Registry 校验请求与结果：

- 无项目且请求完整：execution=pending_module，退出3，不要求先初始化。
- 缺对象或周期：needs_clarification，退出3；未知／不支持的复杂请求不静默猜测。
- 显式选择的项目未完成：initialization_required，带该项目的补全提示。
- 非法输入或不支持意图：退出2；工具故障：退出1。

当前 `/battlecard` 与 `/brief` 的完整意图也返回 pending_module；提醒后续执行所需的我方信息或资产范围，不把路由伪装成完成生成，也不为了缺少尚无提交接口的字段建立无法回答的循环。Codex 自由聊天可按需补问最少信息；不必完成十一题项目问卷。

上述研究路由的 output_path 为空，不采集、不更新档案、不生成报告；独立搜索／抓取／显式摘录工具尚未接入这些路由，自动语义提取、实质核查、Analysis 和 Reporter 仍未实现。聊天确认到最终锁、真实模型及运行时Prompt、跨任务资产复用和增量更新也未接通。Codex 宿主按指令真实执行的研究须另有来源及产物证据，不能从解析、搜索成功或来源快照推导研究完成。

## 可选项目与读者

用户选择长期跟踪、我方对照或固定读者时，可按 [init-project.md](../../../commands/init-project.md) 分批初始化，Python 仍逐题保存。此模式的名单／维度评审和范围锁保持原有约束，详见 [运行流程](research-workflow.md)。默认单对象入口不要求伪造这些批准。

Python访谈ready／退出0仅表示profile已保存，不自动进入维度建议、候选发现、锁定或采集；CLI会说明这层完成边界。Codex聊天建议与确认由宿主单独执行，不会自动迁移为Python最终锁。

未配置读者时输出通用概览；有配置时复用 ReaderProfile，不自动填四类角色。无我方产品时不生成我方优势、反驳或赢输结论；单对象事实画像可正常研究。多角色输出共用同一事实版本。
