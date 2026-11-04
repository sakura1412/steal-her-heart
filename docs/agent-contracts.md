# Agent 标准输入输出契约

版本：0.30.0；2026-11-04。本轮交付可解析的 [Agent 契约目录](agent-contracts.yaml) 和调用／验收规则，均为 design_only。没有新增运行时加载器、Pydantic 类型、Agent 实例、工具注册或权限。实际端口仍见 [interfaces.py](../src/steal_her_heart/interfaces.py)，职责及调用关系见 [模块契约](module-contracts.md)。


状态目标以 [状态机 v2](state-machine-v2.md) 及其迁移目录为准：独立缺口／补研／报告 QA、两个确认等待及 CANCELLED 替代此前内部阶段安排。本文标为当前或迁移前的 v1 边界继续描述未变更源码；新状态与恢复须先实现迁移和守卫后启用。

## 行业与读者契约补充（0.30 目标）

依 [产品规则](product-design.md)，Planner 接收可选 IndustryPackSelection 和 ReaderDecisionSpec，解析字段要求及版本；Analysis 消费解析后的行业方法与读者问题；Discovery 输出逐标签有依据的竞争关系。Reporter 仍只组织核查后的分析，不补造读者策略。YAML 为设计目录，新增类型不表示 Python 接口已实现。

## 十项标准与单一维护源

每个 Agent 必须具备以下十项。完整定义集中在 agent-contracts.yaml 的 agents 中，Prompt 与其他文档引用它，不维护多份可独立变化的权限表。

| 定义项 | YAML 字段 | 必须说明 |
|---|---|---|
| Agent Name | name | 稳定名称；显示名或现有类名的映射明确 |
| Objective | objective | 负责解决的问题及职责边界 |
| Input | input | 命名数据、目标类型、必需／可选、条件必需规则、版本与准入条件 |
| Output | output | 返回的数据、结构／引用约束、空集含义与下游用途 |
| Tools | tools | 已有 Registry 工具、待实现能力、内部服务、模型角色分别列明；未列出的默认不允许 |
| Rules | rules | 执行必须保持的业务不变量及与共同规则的关系 |
| Decision | decision | 可自主判断的范围；判断、提案与最终授权分开 |
| Restrictions | restrictions | 禁止的行为与不能拥有的控制权 |
| Failure Handling | failure_handling | 失败如何上报、保留什么、由谁恢复，不能只写“重试” |
| Completion Criteria | completion_criteria | 可观察的模块产物条件及下游准入，不能仅以模型说“完成”判断 |

目录顶层 spec_version 标识本规范版本，contract_format_version 标识目录格式，两者均不改变当前任务数据库 schema_version。YAML 描述可机器读取的开发定义，input/output 的 type 是目标类型名，不代表仓库已存在这些类；本轮没有可执行的完整 JSON Schema。后续实现必须把描述和条件约束落实到类型、守卫及测试，不能以 YAML 可解析冒充运行契约已生效。

## Agent 清单与现有代码映射

| 规范名称 | 职责 | 当前对应物与缺口 |
|---|---|---|
| Orchestrator | 调度、状态、恢复及最终交付控制 | 有 Orchestrator.run_task／resume 骨架，生产仅停 UNDERSTANDING；未接全链路 |
| PlannerAgent | 需求识别、初始计划、缺口重规划 | BriefRecognizer／Planner Protocol；已有离线识别，动态规划未实现 |
| CompetitorDiscoveryAgent | 候选发现、消歧、分类与匹配依据 | 已有项目型提案／选择契约，无完整发现执行器或非项目空名单协议 |
| ResearchAgent | 获授权工作项的来源采集与候选提取 | ResearchEngine.collect(plan) → EvidenceBundle 仅端口；独立工具不等于 Research 已接通 |
| ValidatorAgent | Research QA 及 Claim 支持核查 | Validator.validate(plan, evidence) → ValidationResult 仅端口及结构 guard |
| AnalysisAgent | 比较、机制解释与条件性建议 | AnalysisEngine.analyze(evidence) → Analysis 仅端口；目标需新增问题、策略、QA 和背景输入 |
| ReporterAgent | 草稿、引用映射及呈现修复 | ReportGenerator.render(analysis) → ReportDraft 仅端口；没有真实报告执行器 |
| ReportQAAgent | 全文九项检查及返工依据 | 无生产接口或执行器；名称表示独立评估职责，不新增自由协商 Agent 或主控 |

需求理解属于 Planner 的 understand 动作，不为了目录重复增加一个理解主控。Worker 是 ResearchAgent 的执行实例，采用 [独立任务调度](task-scheduling.md) 的 ResearchTaskSpec 与 WorkerAssignment；一个竞品可拆多任务，同一 Worker 可先后处理不同对象；它不继承父主控权限。Research QA 是 Validator 的研究核查动作，Coverage Assessment／Gap Analysis 不各增一个控制 Agent。

Memory、Source Manager、证据库、Registry、模型网关与发布器是受控服务，不是额外自主 Agent。它们有独立数据／服务接口，不能因为被 Tools 引用就获得规划、联网或批准权；相关协议见 [证据管理](evidence-management.md)、[采集](data-collection.md) 和 [恢复](failure-recovery.md)。

Research 的 research_task 输入是独立 ResearchTaskSpec，assignment 另绑定当前 Worker、dispatch、租约、权限与预算，不与顶层执行 task_id 混用。Result 按任务及分配返回，未通过接纳的迟到／越权结果不能混入当前资产。八个角色不因 Worker 数量增加而扩为多套主控。

## 共同调用信封（目标）

所有 Agent 继承共同规则，再叠加目录中的专属约束。未列出的权限默认拒绝；专属字段不能放宽用户授权、可信配置、状态守卫或公共事实底线。

AgentInvocationVNext 包含：invocation_id、agent_name、contract_format_version、spec_version／contract_hash、任务与研究修订、动作、scope／plan／policy 及输入清单指纹、具体 payload、上游收据、能力授权引用、预算分配、期限、幂等键及必要追踪关联。主控由可信上下文填入控制字段，不接受模型、网页或子代理自行声明的身份和权限。

新任务在理解前尚无 plan_version 或完整 scope／policy，允许相应引用显式为空并标注阶段；进入采集、分析与报告时则要求相应输入齐全。不能为了凑共同字段伪造计划、锁或 QA。项目配置只在实际项目模式提供，默认名称入口不要求 profile.md 或我方产品背景。

AgentResultVNext 包含：invocation_id、生产者与确切契约版本、输入指纹、执行结果、类型化 payload／工件引用、问题与未完成项、实际调用及预算引用、相关收据和时间。执行结果使用 [失败恢复协议](failure-recovery.md) 的分层语义：执行正常、部分完成、失败、延后、取消、结果不明，与 found／empty、QA 判定和业务完成分开。

调用方验证结果对应当前 invocation、agent、动作及输入版本；拒绝未知字段、错误类型、孤儿引用、过期结果或越权写入。success 不能与缺失必需产物并存；partial 保留成功子项和全部未完成项。模型自填的 producer、预算消费或 PASS 不作为可信运行记录，主控按实际账本与逐项核查复验。

产物按精确版本引用；输入快照只读，输出追加新版本。单一调用执行完毕不表示其输出已被下游接纳。接纳记录须绑定消费者、输入／产物指纹与校验结果；迟到输出归原修订，不能覆盖当前版。幂等重放不触发新的联网、写入或重置预算。

## Tools 的权限含义

目录 tools 有四个互不混淆的字段：registry_tools、planned_capabilities、internal_services、model_role。工具列表只是设计上限，绝不授予本次调用权限。

未来有效权限取交集：可信宿主与用户授权、已注册实现、Agent 工具上限、工作项动作／资源范围、当前预算和执行条件。子代理不能把自己的 tools 列表写入 ToolContext 来提权。当前 Registry 只检查工具 required_capabilities 和输入／输出 Schema，尚无 Agent 级 allowlist 或上述完整交集 guard。

| 类别 | 目录中的名称 | 状态及调用边界 |
|---|---|---|
| Registry 工具 | web_search、web_fetch、extract_features | 已有独立实现；只有 Discovery／Research 的许可列表含所需工具，不代表它们已被生产 Agent 调用。extract_features 只是显式原文范围摘录 |
| 待实现采集能力 | document_parser、website_crawler、news_search、review_collector、structured_extractor | 概念能力 ID，不是已注册工具名；落实 Adapter、契约和授权后才可调用，不动态导入模型指定代码 |
| 模型角色 | planner、discovery、researcher、validator、analyst、reporter、report_qa | 指向未来运行时 Prompt／模型网关角色；模型只能生成本模块约定输出，不能获得任意 SDK 或网络权限 |
| 主控服务 | dispatch_module、commit_checkpoint、decide_recovery、publish_checked_artifact | 仅 Orchestrator 可用；由可信程序执行，部分基础虽已存在，完整版本协议仍待实现 |
| 只读与确定性服务 | read_bound_assets、deterministic_checks | 限已分配资产、许可计算方法与工件校验，不提供任意 shell、路径或查询权限 |
| 追加产物服务 | append_candidate_assets、append_validation_records、append_analysis_assets、write_draft_artifact | 按 Agent 许可仅追加相应产物类型和授权位置；草稿写入不是发布，核查记录不自动使报告成功 |

表中内部服务名均为设计标识，不是现有 Registry 工具；实现时需类型化输入、作用范围、幂等与失败契约。模型可提出结构化请求，实际服务调用、类型校验和权限审计由可信执行代码完成。

Planner、Validator、Analysis、Reporter、Report QA 不直接检索网页。缺资料由 Validator 报缺口，Planner 提计划，Orchestrator 安排 Research。Research 可在工作项允许的来源／查询变体内选择方法，不能据此自行添加问题、对象或补研回合。

## 完成、核验通过与交付的区别

| 返回产物 | 模块调用何时可结束 | 何时允许下游继续 |
|---|---|---|
| Planner 计划提案 | 计划或明确的澄清／不可行分支已返回 | 主控核范围、依赖、能力和预算；需要的真实批准／锁有效 |
| Discovery 候选 | 有依据的候选及缺口／空结果已记录 | 候选核查合格并有真实用户选择，才进入正式多对象研究 |
| Research 采集批次 | 每个分配项有实际结果或明确失败／未完成状态，原件与候选可追溯 | Validator 依据完整当前研究范围评估；采完或有候选不等于充分 |
| Validator 核查结果 | 完成约定检查并返回当前判定；RESEARCH_MORE 可以是正常评估结果 | 当前完整 Research QA PASS 才准入分析；Claim 支持核查另行执行 |
| Analysis 候选分析 | 必需分析产物与证据关系完整，未支持部分明确记录 | 新或变化 Claim 通过独立支持核查，必需目标已满足才交 Reporter |
| Reporter 候选报告 | 完整草稿和片段映射可核对 | 独立 Report QA 合格，随后做实际工件与发布检查 |
| Report QA 评估 | 九项逐项结果与问题可定位；REVISE／STOP 是有效结果 | 只有当前完整 PASS 可用于交付；均分或单句“通过”不能准入 |
| Orchestrator 任务结果 | 调用可因等待、停止或正常调度返回 | 研究最终完成须 QA、确切工件读回、发布基线与状态守卫全部通过 |

结构校验仅证明载荷形状；原文引用存在仅证明引用可定位；语义评估还需独立参考判断。Producer 不自己批准消费者的准入。Research 的 evidence_records 固定为待验证候选；它可以返回零候选，但必须有真实状态解释，不能自动赋予 verified。

用户所说的“已批准研究计划”在默认单对象路径指真实用户范围授权加主控计划准入，不新增强制首次计划确认；可选项目另须正式评审和有效锁。completion_criteria 不得悄悄改变现有入口。

## 失败、模式与 Prompt 的衔接

各定义的 failure_handling 继承统一故障协议；Agent 负责报告分类、工件与影响，恢复权仍在 Orchestrator。语义不充分与评估器崩溃分开，API 故障不转换为 empty，暂不可执行不伪装 WAIT_CONFIRM。返回 needs_input 或恢复建议是提案，不能直接修改当前状态；目标延后等枚举必须先迁移才能使用。

所有模式使用相同 Agent 边界；[Quick／Standard／Deep](research-modes.md) 通过 policy 输入调整任务和证据门槛，不给 Quick 少做质量检查的权限，也不授予 Deep 新工具。多轮修订保留旧资产、收据和预算，只有符合新输入的局部材料可采用。

后续 Prompt 是契约的行为说明，应引用该 Agent 的目标、字段、工具与完成条件；不能创造另一份权限或状态规则。system.md 保存共同约束，七个角色 Prompt 对应目录能力；Orchestrator 是可信控制程序，不要求增加第九份控制 Prompt。生成与评估分开调用也不保证语义正确，仍需独立评测。

## 实施与验收

当前接口与目标类型不一致，不能把 YAML 直接动态加载成可执行 Agent。按单模块顺序落地：共同信封／版本与类型校验 → 对应角色的输入输出与条件分支 → 工具及服务授权守卫 → 真实执行与错误映射 → 消费者准入和恢复集成。每步完成对应测试后继续，保留旧端口与快照的明确迁移／拒绝策略；未知版本不宽松丢字段。

后续验收项如下，本轮仅验证目录及文档一致性，未实现这些运行测试：

| 编号 | 可观察验收 |
|---|---|
| AGC-01 | 八个角色均有十项定义，稳定名称／类型／工具可解析；未知契约和类型版本拒绝，目录不能自行注册能力 |
| AGC-02 | 首次理解允许尚无计划，Research 则拒绝缺范围／依赖／权限的输入；默认单对象不强制项目或完整画像 |
| AGC-03 | Agent／工作项／宿主权限交集真实执行；Planner 搜索、Research 发布、Reporter 迁移状态等越权零执行 |
| AGC-04 | 新模型输出伪造身份、工具权限、预算或 PASS 不获信任；真实调用与引用可对账，外部资料不改规则 |
| AGC-05 | Research 成功返回候选和采集状态，但不能签发 verified；全部工作项恰有结果，空候选与失败／延后正确区分 |
| AGC-06 | Validator 正常 RESEARCH_MORE 与执行错误分开；覆盖完整、语义支持及时效合格才允许分析 |
| AGC-07 | Analysis 新 Claim 独立核查；Reporter 不添加事实／建议，Report QA 不自行修改草稿或发布 |
| AGC-08 | 草稿成功不等于报告完成；九项质量、确切工件读回和发布基线全部通过才 COMPLETED |
| AGC-09 | 输入指纹、计划／策略／资产或草稿变化使旧结果不可直接接纳；迟到、重复与跨修订结果不覆盖当前版 |
| AGC-10 | partial、deferred、unknown 和人工事项按统一协议处理；仅主控恢复，次数与预算不按 Agent 重置 |
| AGC-11 | Quick／Deep、可选项目与多轮变更均保持相同权限和版本边界；子代理仅获分配任务权限 |
| AGC-12 | 契约映射、错误路径及真实消费者做集成验收；YAML 校验、空 Protocol 或固定替身不宣称 Agent 可用 |
