# 测试与验收

版本：1；2026-11-04。

当前验收可以把已实现组件串成受控的参考工作流，并系统检查缺失状态和显式冲突的闸门。**这不是从生产 CLI 跑通的真实竞品研究链路。** 计划构造、实质 Validator、Analysis 和 Reporter 尚未接入时，测试会明确使用替身或场景夹具；替身通过不能算对应生产模块已实现。

## 三个层次分别证明什么

| 层次 | 输入与执行方式 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 单元与真实组件集成 | 调用仓库中的 Schema、状态机、Registry、调用账本、快照与摘录代码；边界数据受控 | 参数／引用／状态约束、失败分类、持久计数、文件完整性和组件接口符合所测场景 | 来源说法真实、模型判断可靠或完整报告质量 |
| 测试专用参考工作流 | 合成 brief 和页面，经已有组件传递；未实现能力由显式替身或夹具承担 | 从输入到临时参考报告的数据传递、来源引用、失败停止和状态 guard 可以在所选场景中协同 | 生产 Orchestrator 已调度这些步骤，或真实 Research／Validator／Analysis／Reporter 已存在 |
| 真实生产端到端 | 用户从生产入口发起，实际采集、核查、分析并得到可追溯报告 | 未来用于证明实际服务、真实证据和产品交付链路可用 | 当前尚未具备，不能由前两层的通过结果代替 |

生产 `/analyze`、`/battlecard`、`/brief` 仍返回 `pending_module`；`run/resume` 仍停在 `UNDERSTANDING`。参考工作流即使把测试中的状态推进到终点，也不会改变实际任务，或使这些命令生成报告。

## 如何运行

在仓库根目录、已有开发环境中执行：

```bash
.venv/bin/python -m pytest tests/acceptance -q
```

完整回归和静态检查沿用：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

确定性验收使用合成数据，不需要搜索或模型密钥。测试替换 HTTP I/O，并用 monkeypatch guard 阻断本协调器使用的已知 socket／subprocess 入口；这是测试内的防误调用措施，属于 Python 层检查，不是操作系统沙箱，也不保证拦截所有旁路。该验收不会重新请求此前受限的公开搜索服务。此前一次搜索验证页与单个公开示例页抓取是独立历史记录，不计为完整研究链路验收。

代码位于 [tests/acceptance](../tests/acceptance/)：[参考工作流](../tests/acceptance/workflow_harness.py)、[链路测试](../tests/acceptance/test_reference_workflow.py)、[边界矩阵测试](../tests/acceptance/test_boundary_matrix.py)、[离线隔离](../tests/acceptance/conftest.py)与[场景夹具](../tests/acceptance/fixtures/workflow_cases.json)。本轮检查、实际计数及实现散列记录在 [workflow-acceptance.json](../validation/workflow-acceptance.json)。历史记录只说明各自记录时的版本；当前是否通过应以当前测试结果为准。

## 参考工作流的真实组件与替身

| 步骤 | 调用的真实部分 | 受控或替代的部分 | 需要保留的边界 |
|---|---|---|---|
| brief 与计划 | BriefRecognitionTool 的离线识别、ResearchPlan 等契约 | FixturePlanner 构造场景任务，未调用真实 Planner 模型 | 规则识别不是实体核验或通用语义理解 |
| 搜索与抓取 | Tool Registry、DuckDuckGoSearchProvider 与解析器、PublicFetchProvider、WebSearchTool／WebFetchTool、调用账本和错误分类 | 只替换 HTTP I/O 边界 `_http_worker`／`_fetch_worker`，返回合成 HTML 或预定错误 | 不证明公开服务当前可访问，也不证明找到了真实竞品资料 |
| 来源与摘录 | SnapshotStore 的写入／重读与散列检查，精确摘录候选及 locator 绑定 | 选择的字符范围与预期引文由场景给定 | 候选仍为 unvalidated，完整性不等于主张获得语义支持 |
| 证据适配 | Evidence 契约 | fixture_semantic_adapter 把选定的精确 quote 原样放入 Evidence，confidence 固定为 0 | 是显式测试替身，不能当作自动语义提取或已校准置信度 |
| 验证 | ValidationResult 契约和状态迁移 guard | ScriptedValidator 对账预期来源与引文后，按预设 oracle 输出结果 | 替身 PASS 不能作为实质 Validator 的正确性验收；给定冲突不等于自动检测冲突 |
| 分析 | Analysis／Evidence 引用契约 | ScriptedAnalyst 只构造合成场景所需的结果 | 不证明比较、机会或威胁具有业务有效性 |
| 报告与交付 | ReportDraft 契约、MemoryStore 与状态机 | ScriptedReporter 组织临时 Markdown，ReferenceWorkflow 协调并检查交付 | 协调器与交付检查也仅属测试，不是生产 Orchestrator／Reporter；不证明 Battlecard 一页版式或报告语义质量 |

测试 oracle 是预先声明的场景真值、预期来源及预期分支。它的作用是检验输入是否按约定传递、异常是否阻断后续动作；它不是独立研究得到的事实，也不是模型自评。失败场景不能通过改成空结果、清空冲突或直接固定 PASS 来“修绿”。

## 系统边界矩阵

| 场景 | 真实检查与受控输入 | 期望的安全结果 | 仍未验证 |
|---|---|---|---|
| brief 有歧义或无可支持对象 | 真实离线识别器；预设歧义与否定输入 | 歧义进入 WAIT_CONFIRM，不支持的输入终止；均不进入研究 | 真实实体消歧、开放语义与模型鲁棒性 |
| 参考成功路径 | 合成 brief、页面和场景 oracle；真实 Registry、快照、摘录、Schema 与状态 guard | 在测试隔离目录生成带来源的 SIMULATION 报告，引用与输入对应 | 生产执行链、真实来源或语义分析能力 |
| 未采集／查找后未找到 | 真实 CollectedField／CompetitorCollection 检查状态、查找记录和范围说明 | pending 保持“未采集”；完成查找且有范围说明才允许 not_found／“未找到”，不产生事实值 | 检索是否真实发生、范围是否充分 |
| 仅访问失败或页面不符合预期 | 受控 unavailable、搜索／抓取访问限制、未知搜索版式或 HTTP 404 | 保留受限／失败，不进入 oracle、分析或报告，不能包装成“未找到”或真实零评价 | 实际网站策略与可访问性 |
| 不适用 | 真实模板字段的 allow_not_applicable、理由与字段集合约束 | 字段允许且有理由才接受；无理由、字段不允许或借此跳过必需定义时拒绝 | 不适用理由的业务真实性 |
| 显式冲突／覆盖不足 | 夹具明确给出 conflicts、gaps 或不完整 coverage；调用真实 ValidationResult 和状态 guard | 不能构造不诚实的 PASS，RESEARCH_MORE 不得直接进入分析；原冲突不能靠静默清空绕过 | 由原文自动识别矛盾、对齐价格／地区／版本及真正化解冲突 |
| 夹具解释旧版／新版差异 | 场景预先声明版本区分与结论，并保留两份真实来源快照 | 临时报告保留旧版、新版和“无自动冲突消解”的说明 | 自动发现版本差异或自动消解冲突 |
| 来源被篡改／正文版本不匹配 | 对真实快照或已选择正文版本制造不一致 | 拒绝加载或绑定，不用坏来源构造成功证据 | 来源发布者身份与内容真实性 |
| 越权或额度不足 | 真实 Registry 能力检查与持久调用额度，提供方可计数 | 失败前不执行未授权请求；额度到限不再新增调用，不因重开实例重置 | 跨工具／模型／活动时间的研究全局预算 |
| 同一调用重放 | 重开账本／工具实例，按相同任务、请求标识和参数读取完成记录 | 重放本地结果，不重复外部请求、不覆盖原来源或原模拟报告 | 未完成调用的全部恢复行为、完整研究任务的阶段恢复或跨任务资产复用 |
| 报告引用错误或目标已存在 | 测试交付检查遇到虚构 source ID，或目标已有工件而拒绝覆盖 | 不登记报告引用，不进入 COMPLETED，不覆盖既有工件 | 生产报告交付与异常恢复实现 |
| 报告写到一半失败 | 注入临时文件部分写入后的 OSError | 清理本次临时半成品，不发布正式模拟报告，不登记引用，不进入 COMPLETED | 任意时刻断电、进程被终止及全部文件系统故障下的恢复 |
| 最终 Memory 提交失败 | 注入 COMPLETED 对应的 MemoryStore 提交失败 | 内存不先进入 COMPLETED；移除本次已发布模拟报告及引用，保存失败状态 | 跨文件与数据库的生产事务、提交结果不明及恢复中的再次故障 |
| 生产入口回归 | 调用实际路由、Orchestrator 与 CLI | 保持真实未就绪状态及空报告路径，模拟产物不会进入生产任务 | 真实生产端到端完成行为 |

边界矩阵把现有结构／状态约束跨字段组合起来验收。它不会把一个合法的“未找到”记录解释为已证明产品不具备该能力，也不会把一条用户评价或同源转载当作多份独立事实。

## 临时工件与结果解读

参考报告标题固定为 `# SIMULATION ONLY — 合成场景，非真实竞品报告`。`SIMULATION-report.md` 与 `SIMULATION-memory.sqlite3` 只写 pytest 的 `tmp_path`，测试工作目录也切换到该目录。已知 socket／subprocess 入口的 monkeypatch guard 会记录调用尝试；即使 Registry 包装了异常，teardown 仍检查该记录。这个检查覆盖本 harness 的已知入口，不构成操作系统级网络隔离。

报告先独占创建 `.SIMULATION-report.md.part` 并完成写入，再用 `os.link` 独占发布为正式模拟报告，禁止覆盖已有工件。对应故障注入验收检查部分写入失败时清理临时文件，最终 Memory 提交失败时清理本次已发布报告及引用。状态迁移先提交 MemoryStore，成功后才更新协调器内存 context，避免提交失败却提前暴露 COMPLETED。上述只是测试协调器在这些已测异常下的处理，不证明生产交付实现、跨资源原子事务或任意崩溃恢复。

测试不向 `research/`、`projects/` 或公开 `validation/` 提交一份伪业务报告；验收 JSON 只记录测试结果、场景、替身范围和散列等检查信息。

模拟报告中的来源属于合成测试材料，不能用于对外研究。测试中的置信度或验证结果也只是夹具数据；不能把它们当作已校准的正确率、实际用户反馈或当前市场事实。

通过本轮验收后，仍需分别实现并验证：实际 Planner／Research 调度、自动语义提取、来源支持和按维度时效核查、自动冲突识别、Analysis、可读 Reporter、全局预算和完整恢复。只有这些能力从生产入口实际运行，并完成来源与报告质量验收，才能将真实生产端到端项标为通过。
