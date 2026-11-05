# 研究状态与迁移

版本 `1.0.0`。本文件是八阶段工作流单源；这是 Codex 须实际记录和核对的执行协议，不是已部署状态机服务。报告偏好与缺对象处理仅引用 [report-delivery.md](report-delivery.md)，不在此复制访谈顺序或默认值。

## 阶段表

| 状态 | 允许动作 | 退出条件 | 守卫 |
|---|---|---|---|
| Interview | 按交互单源识别主题、采用显式或默认偏好；对象明确后可轻量消歧、扫描 | 主题可识别；audiences 和 report_detail 有真实值及取值依据 | 偏好缺省不阻塞；缺对象不能捏造目标；初扫材料仍是候选，计入预算 |
| Plan | 消歧实体、设计 dimensions、候选方法、独立任务和预算 | 至少 1 个 required 问题／维度；scope_version、实体边界和预算写入检查点 | 身份未决材料隔离；行业包及背景知识不能冒充本次证据 |
| Collect | 执行本轮有授权且有预算的 task，保存 raw 引用和候选 Evidence | 本轮 planned tasks 均终止，或未执行项有明确 blocked／取消原因且预算已对账 | 采集成功不等于核验；失败不当无结果；访问限制按连接器规则停止 |
| Validate | 查支持度、时效、独立性、冲突及覆盖，写 gaps 和补研决策 | 所有必需单元已检查；每个 blocking gap 都有补研／受限停止／需输入的具体决定 | 完成检查不等于充分；不能为了通过而删 required；来源与预算按质量策略 |
| Analyze | Planner 按已核验数据选方法并验输入；涉及比较先过 Comparison，再执行方法、写 Claims | 每个被选方法 completed／limited／blocked／skipped 有原因；新结论逐条核查并引用证据 | 被阻塞方法不产出确定结论；无我方背景不编我方赢输策略 |
| Report | 仅从锁定台账组织 Markdown 草稿、引用映射及角色／详略视图 | 草稿与引用映射写入；未覆盖项和有限结果状态可见 | 不从记忆或其他格式报告新增无证据事实；PDF/PPT分别消费同一台账 |
| ReportQA | 按 RULES 做完整九项内容检查并定位 issues | 内容 QA passed，或已记录问题及有界回退／停止决策 | 自评分不代替核查；问题未解决不以完整报告进入交付 |
| Delivered | 导出默认 PDF 或明确请求的格式，打开／渲染检查；按交互规则处理可选 PPT | 文件验证成功且引用可用；否则记录失败／受限结果与下一步 | 此状态表示交付阶段，不自动等于任务完成；聊天正文不冒充文件成功 |

方法选择发生在证据 QA 后，候选方法可先验输入以识别缺口；真正比较计算与方法执行在 Comparison 准入之后。不能为了形式上走完 Analyze 而跳过缺失输入。

## 允许的迁移与触发

所有迁移由主控决定并记录；Worker 不能改主任务阶段。下列规则以同一 scope_version 和输入快照为前提。

| 从 → 到 | 触发与必要记录 |
|---|---|
| Interview → Plan | 主题足以规划，偏好有显式／配置／默认依据；复用初扫材料及已耗预算 |
| Plan → Collect | required 单元和任务已定义、身份范围可用、预算有余额；写范围检查点 |
| Collect → Validate | 本轮任务与失败／受限项全部可对账，原文和候选记录已保存 |
| Validate → Plan | 需要修订框架、拆分补研任务或真实范围变更；保留旧范围、缺口与授权依据 |
| Validate → Collect | 框架不变，已有明确补研任务、预算及预期改善；补研轮次先预留 |
| Validate → Analyze | sufficient，或有限证据子集可支撑明确的部分问题；后者携带 limited 状态及排除的判断 |
| Validate → Report | insufficient 且已停止研究时只生成未完成诊断；不产出事实性完整分析 |
| Analyze → Plan | 方法必需输入缺失或新结论缺证，记录补研问题；不足额度时改为有限结论或停止 |
| Analyze → Report | 进入草稿的 Claims 已核对，方法状态与缺口明确 |
| Report → ReportQA | 草稿及引用映射完整；输入台账版本锁定 |
| ReportQA → Report | 措辞、重复或组织问题；扣报告修正及全局返工额度 |
| ReportQA → Analyze | 推导、计算或比较有误；记具体问题和受影响 Claims，按全局返工上限执行 |
| ReportQA → Validate | 来源支持、时效或冲突有误；重新检查受影响单元，必要时经 Validate 安排补研 |
| ReportQA → Plan | 范围或必需问题设计错误；保留范围修订和真实授权，重新走受影响步骤 |
| ReportQA → Delivered | 当前范围下的完整／有限正文均已通过内容 QA；本轮需要交付的格式已确定 |
| Delivered → Report | 文件排版或导出可修复且有剩余额度；内容未变也须重新检查完整导出文件 |
| Delivered → Validate | 导出前发现事实过期或输入快照变化，撤销待交付版本的通过状态，重核证据 |

不在表内的跨阶段跳转不能直接执行。当前阶段可以等待一个明确输入、暂时受阻、失败或取消，这些是 workflow.status，不是默默跳过阶段。恢复须先核台账、作用范围、operation_key 与预算，再从相应阶段继续。终态 completed／cancelled／failed 不原地重开；继续工作创建新修订并关联旧快照。

用户中途明确改范围时，先停止依赖旧范围的未开始任务，保存检查点，在新修订进入 Plan；只重做受影响部分。仅改变读者或版式且事实足够时在新修订进入 Analyze 或 Report，不再全量采集，不重置预算。

## 完成、受限与失败

- `workflow.state` 表示阶段；`workflow.status` 取 active／waiting_input／blocked／completed／cancelled／failed。偏好默认可用时不得仅因未回复而设 waiting_input。
- `report_qa` 为 pending／passed／failed，只表示内容检查；`delivery_status` 为 not_started／exporting／verified／failed／text_only，单独表示文件交付。
- `workflow.status=completed` 要求 state=Delivered、report_qa=passed、delivery_status=verified、issues 为空，并已保存实际报告路径、哈希和检查记录。manifest.phase 同时为 completed。
- `manifest.qa.status=limited` 可交付经过 QA 的有限报告；报告标题、范围和缺口必须明确。任务完成不等于研究充分。
- 用户明确只要聊天文本时，文本就是选定交付形式，检查后可 verified；默认 PDF 导出失败后暂给文本只能 text_only，workflow.status 保持 blocked 或 failed，不能仅凭降级声明 completed。
- 可选 PPT 未要求或尚未答复不阻塞已核验的 PDF 完成。用户原先明确要求两个格式时，两者都成功才能完成；完成后新要求 PPT 则建交付修订，复用同一输入台账。
- 预算耗尽或返工无进展时停止对应回路，保留有限结果或诊断；取消和执行失败不得伪装成“没有资料”。质量与轮次上限见 [research-quality.md](research-quality.md)。

## 台账字段与兼容

新任务使用 manifest 契约 `1.2.0` 的 `workflow` 对象，记录上述 state／status／report_qa／delivery_status、issues 和最近迁移（from、to、reason、scope_version、发生时间）。跨阶段迁移另追加 event，使用 `event_type=checkpoint`、唯一 operation_key 和前后台账引用，不改变既有事件类型来伪造迁移执行。

为兼容旧读取方，phase 使用既有枚举：Interview／Plan→understanding，Collect→researching（补研时 supplementing），Validate→validating，Analyze→analyzing，Report／ReportQA／Delivered 未完成→reporting；blocked／cancelled／failed／completed 由 workflow.status 覆盖 phase。waiting_input 保留所在阶段映射并明确待输入原因。

1.0.0／1.1.0 旧快照仍可读；没有 workflow 字段时不得仅凭旧 reporting 推断已经通过 ReportQA。恢复时查看实际台账和文件检查记录，在新 1.2.0 修订补齐状态，依据不足则 blocked。Schema 仅检查结构与可表达的组合约束；它不能证明原文支持、真实迁移或文件检查已发生。
