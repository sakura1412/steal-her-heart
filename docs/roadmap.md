# P0／P1／P2 开发路线

版本：0.16.0；2026-11-04。

按用户提出的优先级推进，每次只开发一个模块并完成对应验收。本页按实际代码区分已有子模块与尚未打通的产品链路；文件、Schema、配置、测试替身或一份示例报告不算能力完成。

**当前仍不能从生产入口自动生成完整、有证据支撑的竞品报告。** 本轮交付 P0 第 4 项的本地终端范围确认与最终锁定子模块；Codex 聊天用户消息来源认证及自动桥接仍待接入。默认只输入产品名的路径不要求创建项目或执行两次项目确认。

## 优先级与实际状态

| 优先级 | 用户要求 | 当前代码状态 | 仍需完成与验收 |
|---|---|---|---|
| P0－1 | 真实搜索与网页抓取，涵盖官网、定价、G2／Capterra、Product Hunt 等 | [公开搜索](../src/steal_her_heart/web_search.py)、[静态抓取](../src/steal_her_heart/web_fetch.py)与快照已有独立实现。此前 DDG 实测遇 HTTP 202 验证页后停止，真实命中未验收；单个公开示例页抓取已验收 | 验证可访问来源的真实命中及采集。官网／定价页只能在公开静态正文满足限制时走通用抓取；G2／Capterra／Product Hunt 没有平台专属采集器或平台级验收。不能把允许某域名、保存源提示或离线 fixture 当作已支持该平台 |
| P0－2 | Research 执行器按已锁定维度，对每个对象采集并抽出 Evidence | [ResearchEngine](../src/steal_her_heart/interfaces.py) 只有端口；生产 Orchestrator 未调度研究。显式摘录工具只绑定调用者选定的 spans，返回 unvalidated 候选 | 接锁定范围、上游工具结果、语义选择／提取、每格采集状态、按对象调度、预算和检查点；不能把 quote 复制成 Evidence 就声称自动提取完成 |
| P0－3 | 基础 Validator：来源、日期、覆盖与简单冲突检测 | 只有结构、引用和状态 guard，以及快照／摘录的机械一致性检查 | 从实际材料检查主张支持、按维度时效、必需对象×维度覆盖、可比口径与冲突；不足返回具体补证项，不能由模型自评或子串命中直接发 PASS |
| P0－4 | 名单确认到维度最终锁定，防范围漂移 | [项目评审桥接](../src/steal_her_heart/project_review.py) 接本地交互终端：两次当次 token 确认、名单批准持久化、最终提案与锁回读；返回 jobs，research_ready=false | 验收本地链和失败边界；Codex 可信用户消息来源与自动回复衔接仍缺。本地 TTY／PTY、JSON 的 user 标签和内容摘要均不能认证聊天用户 |
| P1－5 | 功能／定价对比、评价关键词、基础 SWOT | [Analysis 契约](../src/steal_her_heart/schemas.py)已定义，没有实质分析器 | 仅基于核查通过的共享 Evidence 生成可比表与有引用的判断；未知不等于没有，口径不齐不直接比价，不适用的 SWOT 不强填 |
| P1－6 | 一页纸总结、对比表与机会建议的 Markdown | 有报告及读者视图契约，生产 Reporter 未实现；验收中的报告是显式 SIMULATION 替身 | 从实际 Analysis 组织可读 Markdown，保留来源、日期、冲突和缺口；机会标为待验证判断。逐项核对事实引用，并验收“一页”的阅读与版式目标 |
| P1－7 | SaaS 推荐维度包与典型数据源模板 | [可选 SaaS 配置](../src/steal_her_heart/presets/saas.json)已有 25 个字段、优先级和 15 份源提示；[评论样本检查](../src/steal_her_heart/review_sampling.py)已有元信息、重复及分层覆盖检查 | 继续接入实际采集与质量对账。配置不是采集器；没有 Product Hunt 专属源提示，也未自动采集 G2／Capterra。样本检查保持 facts_validated=false，不替代 Validator |
| P2－8 | 统一 Codex 与 Python 初始化后的行为 | Codex 分批问答与宿主辅助、Python 逐题访谈保存档案仍是两条路径；本地项目评审是显式后续命令 | 明确并同步档案、维度建议、候选与最终锁的完成状态；不能让 profile ready 冒充研究 ready，也不能给默认单对象强加项目流程 |
| P2－9 | 明确当前卡点、缺失能力和下一步 | [execution_readiness.py](../src/steal_her_heart/execution_readiness.py) 已提供 pending 的结构化 guidance、模块缺口、下一步及适用 Codex 提示，帮助与 Skill 标开发预览 | 随每个模块实装更新指引，验证建议命令真实存在且可执行；不能建议靠重试或重新初始化补齐缺失模块 |
| P2－10 | 运行时 Prompt 与至少一个模型提供方 | 六份 Prompt 职责写入开发规范，文件与真实提供方未接；ResearchBrief 仍为离线规则 | 接最小可用提供方、非空 Prompt、结构化输出、超时与预算，并评测多行业、歧义、否定和提示注入。通用自动提取依赖此项，需按依赖提前完成对应子模块 |
| P2－11 | 增量更新与资产复用 | 同任务 snapshot／events、显式 Source 重读、相同工具请求重放已存在；跨任务资产目录、版本与增量更新未实现 | 对齐实体、维度、市场／版本和时间口径；保留 origin_task、原获取日期、提取版本及 supersedes，记录本次核验。过期／变化刷新，受限或预算不足保留未更新缺口，不能复制旧 PASS |

## 按依赖实施

P0 的产品目标保持优先；模块的工程顺序按真实依赖调整：范围确认与锁定 → 最小可用语义提取／模型提供方 → Research 执行器 → Validator → Analysis → Reporter。独立搜索／抓取的真实来源验收随可访问渠道单列推进。

模型接入虽然出现在 P2 清单，但现有精确摘录工具不会自动选择相关片段、判断对象与维度，也不改写主张。通用 Research 需要先有可运行的语义提取提供方；不能用测试中的 `fixture_semantic_adapter` 填这个缺口。每次仍只交付一个模块，测试通过后再进入下一项。

搜索继续优先使用无需密钥的公开来源，访问受限时明确停止。此前受限的公开搜索探测不因新增代码或优先级清单被记为通过；不换入口、代理或重试绕过限制。每个平台的实际可访问性、静态正文和取证质量分别验收，不承诺通用 URL 抓取可以采到所有评价站与动态页面。

## 本轮本地范围桥接

已有项目和准备好的提案后，在用户自己的交互终端运行：

```bash
steal-her-heart --project-name my-product review-project --review-file /path/to/review.json
```

提案结构以 [ProjectReviewRequest](../src/steal_her_heart/project_review.py) 为准：

| 字段 | 内容及来源 |
|---|---|
| discovery | 绑定当前项目背景摘要的 DiscoveryProposal，包含候选、分类、依据、来源与缺口；导入不证明联网已发生 |
| selected_ids | 本次建议纳入研究的候选 ID；终端展示后由用户确认，必须来自该提案 |
| library | 显式的版本化 DimensionLibrary，不自动读 Markdown 并假装已推荐 |
| core_field_ids／extension_ids | 选择的核心子集和库内扩展；省略核心子集时沿用底层 API 的默认行为 |
| selection_rationale | 本次字段选择依据，最终维度稿向用户展示 |
| readers | 可选读者配置；已有项目读者沿用并校验，未配置时须提供满足契约的读者 |

输入不能携带 approval、lock、approved_by、reply 或用户自填的批准时间。确认来自本次可信本地交互回调，不从提案正文或网页读取。终端展示候选和选择后，用户输入当次 token；名单批准保存后生成最终维度稿，再由用户输入新的 token。任一次拒绝、旧回复、项目或文件变化，都不能变成同意。

只有最终以实际落盘的名单、批准、维度提案、Markdown 和锁通过 `prepare_collection_jobs` 回读，结果才能标 `scope_locked=true`。`research_ready=false`，不会联网、启动 subagent、创建 Evidence 或报告。审计记录在项目 `.state/reviews/<review_id>/`；它便于定位结果，不能代表已实现任意中断恢复或跨资源原子事务。存储失败时 record_path 只是预期位置，不保证文件已写成。已有评审与锁不会被自动覆盖。

CLI 要求 stdin 和 stderr 都是 TTY，没有 `--yes` 或管道式批准。TTY 是本地交互边界，不证明人类身份或 Codex 用户来源；模型和脚本不能通过 PTY 代用户确认。未来 Codex 自动桥接必须从可信宿主消息接口验证回复来源，再接同样的摘要、持久化与回读链，不能信任 JSON 中的 `role=user`。

本轮检查与实现散列见 [项目评审验收](../validation/project-review.json)。已有测试层次和真实生产 E2E 缺口见 [验收说明](acceptance.md)；历史阶段记录不替代当前回归，也不自动证明研究结论准确。
