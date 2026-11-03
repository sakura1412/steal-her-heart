# Python Agent 首期任务与验收契约

spec_version：0.1.0；wire schema_version：1。本文定义待实施工程；规范交付不代表已有可运行内核。

## 1. 目标与边界

实现可配置、可恢复、可扩展的单 Agent。首个闭环：读取授权目录中的 UTF-8 Markdown／文本资料，生成带证据引用的 Markdown 报告与 JSON 结果。运行约束以 RULES R01–R09 为准。

假设本地可信单用户、SQLite 本机存储、资料运行期间只读。云端多租户、分布式调度、网页采集、长期记忆、任意代码执行和多 Agent 不在首期范围。业务明确后增加任务、工具和评测，不硬编码示例事实。

## 2. 工程与 CLI

以下是实施目标结构，本次不创建空工程：

```text
pyproject.toml + 一种锁文件
src/agent_core/
  contracts.py       # Task / Decision / ToolResult / Result
  engine.py          # 唯一执行循环和状态迁移
  policy.py          # 权限、路径、预算
  registry.py        # 显式插件注册
  store.py           # SQLite 状态、调用、证据、事件
  prompts.py         # 版本化指令与上下文
  cli.py
  models/            # 离线替身与真实模型适配器
  tools/             # 本地资料工具
  evaluation/        # 语义与引用评测
tests/{unit,integration,recovery,fixtures}/
examples/            # 离线配置、任务与资料
```

实施后的命令契约：

```bash
python -m agent_core.cli run --task examples/local-brief.json --config examples/offline.json
python -m agent_core.cli status --run-id <run-id>
python -m agent_core.cli resume --run-id <run-id>
python -m agent_core.cli provide-input --run-id <run-id> --input-file answer.json
python -m agent_core.cli cancel --run-id <run-id>
```

run 打印内核生成的 UUID run_id；用户 task_id 不直接拼接路径。数据库、documents_root 和 output_root 由可信配置指定。退出码：0 成功，1 运行失败，2 输入／配置无效，3 等待，4 取消。status 只读成功返回 0，JSON 中包含实际状态。等待态 resume 无新输入或授权时返回 3，不重复执行。

首期保留授权状态与恢复协议，不启用外部写工具。未知副作用恢复以测试插件验证。

provide-input 的 answer.json 为带 kind 的严格对象：补充信息使用 `{kind:"clarification",run_id,question_id,answer}`；授权回复使用 `{kind:"approval",run_id,approval_id,action_hash,decision,resolution}`。decision 为 approved/denied，resolution 为 not_applicable/completed/not_started/unknown。WAITING_INPUT／WAITING_APPROVAL 的待处理 ID 由 status 返回，必须匹配；已消费或过期回复拒绝。输入必须来自用户接口，不能由模型或工具结果调用该入口。授权由 store 记录后 resume，未知副作用仍遵循 R06 对账要求。

## 3. Task 与配置

```json
{
  "schema_version": 1,
  "task_id": "local-brief-001",
  "goal": "根据资料比较 A 与 B 的部署方式和扩展接口。",
  "requirements": [
    {"id": "deployment", "text": "分别说明 A 与 B 的部署方式"},
    {"id": "extension", "text": "分别说明 A 与 B 的扩展接口"}
  ],
  "scope": {
    "document_paths": ["a.md", "b.md"],
    "allowed_tools": ["list_documents", "read_document"]
  },
  "output": {"language": "zh-CN", "formats": ["markdown", "json"]}
}
```

拒绝未知字段、重复 requirement ID、空 goal／requirements、越界路径和未知 schema_version。路径相对 documents_root；allowed_tools 是配置启用工具子集。输出固定在 output_root/run_id，不允许模型指定路径。首期语言 zh-CN／en，格式 markdown／json，至少一种。

默认有效配置如下，属于可调整起始值，调整后保存快照：

| 参数 | 默认值 |
|---|---:|
| max_model_calls | 20 |
| max_tool_calls | 30 |
| active_timeout_s | 180 |
| model_timeout_s / tool_timeout_s | 45 / 15 |
| max_attempts（含首次） | 3 |
| max_corrections（跨轮累计） | 2 |
| max_context_tokens / max_output_tokens | 12000 / 2000 |
| max_total_tokens | 60000 |
| wait_expiry_s | 86400 |
| max_document_bytes | 1048576 |
| max_tool_result_bytes | 65536 |

每次实际调用含重试先预留一次调用额度，发出即消耗。每次模型请求按输入 token 与最大输出预留累计额度，返回按真实 usage 结算；超时用量未知保留预留额。适配器不能可靠计数时需提供安全上界或拒绝启用该预算模式。运行前拒绝非正上限、不一致范围和单次超时大于总活动时间的配置。

max_context_tokens 指单次输入与最大输出之和，不能超过所配模型限制；上下文装配超限时先压缩历史和正文，保留任务、规则、schema 与证据索引。必要内容仍无法放下则 CONTEXT_TOO_LARGE，不静默删除规则。

## 4. Decision 契约

每轮只返回三种 JSON 形状之一，全部对象拒绝额外字段；运行时验证类型、长度、工具与引用。

```json
{"kind":"tool","tool":"read_document","arguments":{"path":"a.md","start_line":1,"end_line":40},"reason":"读取 A 的部署和扩展说明"}
```

```json
{
  "kind": "finish",
  "findings": [{
    "requirement_id": "deployment",
    "kind": "fact",
    "text": "A 支持本地部署。",
    "citations": [{"evidence_id":"ev-a-1","start_line":2,"end_line":2}]
  }],
  "limitations": []
}
```

```json
{"kind":"clarify","question":"目标部署环境是本机还是私有云？","blocking_requirement_ids":["deployment"]}
```

finish 例仅展示形状，未覆盖示例任务全部要求，不能通过验收。finding.kind 为 fact／inference，text 非空，citations 至少一项；requirement_id 必须存在。clarify 至少关联一个有效 requirement，存在合理继续路径时不等待。

证据仅来自本 run 成功 read_document 的登记结果。未知 kind／额外解释文字返回 INVALID_DECISION；未知工具同样拒绝；参数与权限错误分别为 INVALID_ARGUMENT／PERMISSION_DENIED。无效提案最多纠正两次，均不执行非法工具，超过上限 FAILED。

字段长度上限：goal 4000 字符，requirements 最多 20 项且每项 text 1000 字符，reason 500，question 2000，finding.text 4000，findings 最多 100 项，每项 citations 最多 20，limitations 最多 20 条且每条 2000 字符。文本 ID 为 1–128 字符，路径受文件系统及策略限制。

## 5. 插件、模型和存储接口

以下为自定义接口形状，不是 SDK 原生签名；实施时补齐类型定义和运行时 schema：

```python
class Tool(Protocol):
    spec: ToolSpec

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> ToolResult: ...


class ModelAdapter(Protocol):
    async def decide(self, request: DecisionRequest) -> ModelResponse: ...


class RunStore(Protocol):
    def load(self, run_id: str) -> RunSnapshot: ...
    def commit(self, expected_version: int, lease_token: str, change: RunChange) -> RunSnapshot: ...
```

ToolSpec：name、version、description、input_schema、output_schema、required_capabilities、side_effect（none/local_write/external_write）、idempotency（safe/keyed/never）、timeout_s。同名工具首期只启用一个版本；重复注册或非法 schema 启动失败。

ToolContext 由内核构造，含 run_id、call_id、deadline、受限资源访问器与取消信号，不暴露全部凭据或数据库。进程内插件仍是受信任代码；类型协议不能隔离恶意插件，首期不执行不可信插件。

ModelResponse 含 raw_decision、usage（input_tokens/output_tokens/是否已知）、provider_request_id。离线替身消费脚本化决策但使用相同校验链。真实模型适配器转换为同一协议，provider/model/endpoint 显式配置；拒绝和错误响应结构化处理。

RunChange 包含新状态、预算变化、事件、调用与证据增量，commit 在同一事务检查版本和租约后提交。禁止多个无事务 save 造成半完成。

| 工具 | 严格参数 | 返回 data | 能力 |
|---|---|---|---|
| list_documents | 空对象 | scope 内存在的普通文件相对路径及 bytes，排序 | read_documents |
| read_document | path:string、start_line:int、end_line:int | evidence_id、path、sha256、total_lines、实际行范围、带行号 text、truncated | read_documents |

只允许 scope 中的 .md／.txt，严格 UTF-8。行号从 1 开始、闭区间、每次最多 200 行；end_line 超末尾截到末行，start_line 超末尾返回 INVALID_ARGUMENT。空文件 EMPTY_DOCUMENT，过大 DOCUMENT_TOO_LARGE，编码错误 INVALID_ENCODING。

结果超字节上限按完整行截断，实际 end_line 为最后完整行且 truncated=true，可从下一行继续。单行也放不下返回 RESULT_TOO_LARGE，不登记虚假证据。保存证据实际行快照与文件散列，后续验证不重新读取可能变化的源文件。

统一 ToolResult：

```json
{
  "schema_version": 1,
  "call_id": "内核分配的 UUID",
  "tool": "read_document",
  "tool_version": "1.0.0",
  "status": "ok",
  "data": {},
  "error": null,
  "duration_ms": 12
}
```

ok 时 data 满足 output_schema 且 error=null；error 时 data=null，error 为 `{code,message,retryable,execution_status}`，execution_status 为 not_started/completed/unknown。内核仍须验证 retryable。call_id、tool、version 匹配意图；duration_ms 非负。非法结果记 TOOL_PROTOCOL_ERROR，不纳入证据。

内核 renderer 发布最终文件，不提供模型任意写文件工具。外部写工具需另行实现预检、授权、幂等、对账和对应测试才能启用。

## 6. Result、验证与发布

结果 JSON 含 schema_version、task_id、run_id、status、findings、limitations、requirement_checks、artifacts、usage、error。status 使用 RULES 状态名。requirement_checks 每项为 `{id,status,evidence_ids,note}`，status=met/unmet，由 verifier 产生而非模型填写。usage 区分已知用量和未知预留。成功 error=null，失败 error 为 `{code,message}`。

成功要求至少一条 fact、所有 requirement 均 met、RULES R07 全通过。无法从资料获得必要事实时保留部分 findings、标记 unmet 并 FAILED，error.code=INSUFFICIENT_EVIDENCE；写 limitations 不足以豁免必要要求。等待输出待处理原因；后续在原 run 接续。

产物名固定 report.md／result.json。报告包含目标、事实、推断、证据和资料缺口；引用能映射到快照。JSON 由内核序列化已验证数据，不直写模型原文。store 中产物清单含路径、媒体类型、字节数和 SHA-256。

避免自引用散列：result.json 的 artifacts 仅列其他产物，其自身散列只在 store 完整清单中保存。失败／取消可保存 partial-result.json，状态真实且不伪装成成功。恢复对账发现内容不符不得静默覆盖已发布文件。

## 7. 里程碑

| 里程碑 | 行为 | 验收 |
|---|---|---|
| M1 内核闭环 | Task/Decision、循环、两个本地工具、离线模型、策略、预算、验证与报告 | A01–A06、A10、A12 |
| M2 持久恢复 | SQLite、事件事务、租约、等待／取消／恢复、发布对账 | A07–A09、A13 |
| M3 扩展与接入 | 新测试插件、真实模型适配器、Prompt 版本、语义评测、CI 与示例 | A11、A14–A15 |

M1 可使用内存 RunStore；M2 才宣称跨进程恢复。M3 的真实适配器必须有离线契约测试；真实付费调用不强制，在授权和配置齐全时单独验证。实施时核对官方 SDK 文档并锁定依赖。

## 8. 验收矩阵

实施阶段为每项提供测试输入和可复查结果，下表不是已通过声明。

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| A01 | A 第2行本地部署、第3行 Python 插件；B 第2行云部署、第3行 HTTP 扩展 | 两项要求 met、四个预期事实、正确路径／行引用、SUCCEEDED、产物匹配 |
| A02 | 畸形 Task、额外字段、未知版本、无效模型决策 | 输入提前拒绝；提案有限纠错或失败；零非法执行 |
| A03 | ../secret.txt、绝对路径、越界链接、scope 外文件 | PERMISSION_DENIED，零读取／证据登记，日志无正文 |
| A04 | 资料含忽略规则并上传密钥；离线模型提出越权调用 | 能力不扩大，无外传，越权提案被策略拒绝 |
| A05 | 工具两次暂时失败后成功；另测权限错误 | 暂时失败总尝试恰3次；权限错误执行0次；预算计入重试 |
| A06 | 无限调用、反复错误、时间／token 临界 | 上限内终止，零额外调用，FAILED 且错误明确 |
| A07 | 意图提交后、结果提交前杀进程并恢复 | 只读安全恢复；已提交结果不重放；预算与事件一致 |
| A08 | 双进程 resume、租约过期后旧进程提交 | 仅有效持有者运行，旧 fencing token 被拒绝 |
| A09 | report 写完而 JSON 未发布时崩溃；终态多次恢复 | 对账后才成功，文件散列一致，终态零新调用 |
| A10 | 缺资料、空资料、假证据、越界引用、事实写反 | 缺口或引用被拒绝；写反由语义夹具识别 |
| A11 | 注册第三个测试工具、替换模型适配器 | 仅改插件、注册与测试，engine 不改；协议错误被拒绝 |
| A12 | 无效 UTF-8、超大文件、超长单行、分页 | 对应错误明确，截断可见，分页行号正确，内存受限 |
| A13 | 等待补充／超时、执行中取消、未知副作用恢复 | 正确接续或终止，取消后零新调用，未知动作不重放 |
| A14 | 离线测试、静态检查、日志植入假密钥 | 检查通过，日志／产物无密钥，CI 无网络和真实凭据 |
| A15 | 提供方契约测试及可选真实调用 | 协议／拒绝／usage 处理正确；真实调用独立标 passed/failed/not_run |

未知副作用用受控测试插件验证，不在生产配置开放。M3 语义夹具至少包含正常资料、缺证据、冲突资料、提示注入四类，每类至少3份。冲突必须保留双方证据，不能擅选一方当确定事实。

实施后的检查命令：

```bash
python -m pytest tests/unit tests/integration tests/recovery -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src/agent_core
```

所有确定性不变量通过；离线语义夹具的必需事实、缺口与冲突判断全部满足；全新临时目录能运行 A01。真实模型按必要事实、引用和缺口独立评测，不要求逐字一致；首期不承诺开放业务成功率或延迟 SLA。

## 9. 完成定义与变更

工程完成需可安装包、锁文件、CLI 示例、M1–M3 实现、离线验收证据、规范一致性说明及真实 API 验证状态。三份文档通过检查只表示规范可交付。

新增业务先给出目标、输入、权限、输出、完成条件和失败例，再决定扩展 Task、插件还是内核。破坏性变更记录兼容影响并保留旧运行的迁移或明确拒绝策略。
