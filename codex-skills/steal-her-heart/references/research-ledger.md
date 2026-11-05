# 研究台账、上下文与恢复

检查点契约版本 `1.3.0`（读取兼容 1.0.0／1.1.0／1.2.0，旧快照不原地迁移）；记录契约仍为 1.1.0。新任务通过 [workflow](workflow.md) 字段区分阶段与交付状态，使用 JSON Schema 2020-12：[逐条记录 Schema](../schemas/ledger-record.schema.json)、[检查点 Schema](../schemas/research-manifest.schema.json)。这定义 Codex 使用实际文件工具维护的工件，不代表已经部署数据库、迁移器或后台任务服务。JSON Schema 检查结构；下述引用、时效、预算及语义检查仍须实际执行。

## 版本目录命名

`<revision>` 使用 `YYYYMMDDTHHMMSS+0800-rNNN-<8位随机十六进制>`，例如 `20261105T103000+0800-r001-a1b2c3d4`。时间采用用户指定时区，未指定时记录实际采用时区；NNN 为当前 subject 下递增序号。随机后缀防同秒冲突，创建前检查路径不存在，不覆盖。`safe-subject-id` 使用经过清理的短标识和稳定实体 ID，不把原始用户字符串当路径。此命名只表示工件版本，不表示产品版本或事实生效时间。

`manifest.snapshot_id` 取该版本目录名，`parent_snapshot` 指前一完整检查点 ID 或 null。恢复在新目录写检查点，旧目录只读；相同事实快照的格式重排可在 reports 下使用 `report-rNNN.pdf/pptx` 并追加报告记录。

## 目录和权威数据

```text
research/<safe-subject-id>/<revision>/
  manifest.json
  framework.json             本次多业务研究框架
  questions.jsonl            问题、假设、支持与反证及验证状态
  sources.jsonl
  entities.jsonl
  relationships.jsonl
  evidence.jsonl
  claims.jsonl
  conflicts.jsonl
  coverage.jsonl
  tasks.jsonl
  events.jsonl
  raw/                       获准保存的页面正文、文件或截图
  reports/
    report.pdf
    report.pptx              仅明确需要时
    reports.jsonl            报告文件、引用映射和检查结果
```

JSONL 每行一个 UTF-8 JSON 对象；空文件表示尚无记录。统一外壳：`schema_version`、`record_type`、`record_id`、正整数 `revision`、`recorded_at`（含时区）、`supersedes`（前版精确引用或 null）、`data`。引用统一为 `{record_id, revision}`，不使用“最新那条”隐式关联。

| 文件／类型 | 关键内容与用途 |
|---|---|
| sources／source | URL、来源性质、原始生产者、独立来源组及依据、获取／发布／内容时点、快照路径与 SHA-256、访问状态 |
| entities／entity | 12 类实体、规范名、多语言别名、身份状态、有效时间与证据；保留稳定 ID |
| relationships／relationship | 两端实体版本、关系类型、情境、有效时间、证据及争议状态 |
| evidence／evidence | 原文片段、定位、URL、获取时间、来源类型、来源精确引用、主体／维度／适用条件、有效期、置信理由与核验状态 |
| claims／claim | 事实或分析结论、是否关键、事实时效类别、支持／反证引用、冲突、置信理由、推导／计算与限制 |
| conflicts／conflict | 冲突证据与对象、受影响结论、处理状态和解决依据；不可静默覆盖 |
| coverage／coverage | 范围版本、对象与维度及条件、必选性、适用性依据、覆盖状态与结论引用 |
| tasks／task | 问题、依赖、输入输出、子任务状态、操作标识、失败类别和下一步 |
| events／event | 变更／工具／QA／恢复事件、前后引用、理由、操作状态与各项预算增量 |
| reports／report | 格式、实际文件与哈希、输入检查点 ID 和 manifest 哈希、段落／表格／幻灯片到 Claim 的映射、内容／渲染／引用检查 |

`claims.supporting_evidence` 是 claim→evidence 关系单源，支持多对多；evidence 再指向 source 及原文。证据中的 URL、日期和类型为便于审计保留的副本，必须与引用的 source 版本一致。不要在 evidence 重复维护另一套 claim_ids：反向追溯通过扫描精确引用构建临时索引，报告段落→claim→evidence→source 和 source→evidence→claim→报告位置均可恢复。

`confidence` 是 high／medium／low／unknown 加理由，不是模型自信百分数；高置信不等于已核验，verification_status 和语义准入单独判断。候选、过期或争议材料可留存，但不能当作当前已验证依据。无来源的重要报告事实禁止先写再补台账。

## 问题与框架绑定

新任务在 Plan 维护 [framework.json](../schemas/research-framework.schema.json) 和逐行符合 [问题 Schema](../schemas/research-question.schema.json) 的 questions.jsonl；两者不是 Evidence。manifest 1.3.0 用 `research_design` 记录实际文件哈希、框架身份／版本、问题记录数及 scope_version。Interview 和尚在设计中的 Plan 可暂缺；进入 Collect 及之后阶段必须绑定设计工件。旧快照不原地升级，恢复时根据实际记录建新修订，不能凭行业标签补造历史问题或验证状态。

加载与接纳时检查：设计 scope_version 与 manifest 一致；question_id＋revision 唯一；parent_question_id、框架 question_refs 和维度 question_refs 均可解析，问题树与维度依赖无环；维度类别 ID 存在；required 项被选中；所有实际任务绑定问题／维度。框架与问题版本变化后重查引用和覆盖分母，保留旧快照与预算。若仅修订假设，不把旧范围批准自动解释为新增对象授权。

每个问题的 supporting_evidence／opposing_evidence 与 claim_refs 指向现有台账，current_conclusion 是对应 Claim 的可读摘要，不成为第二事实来源。支持、反对证据引用都须经过 Validator，操作键须能对账；verification_complete 的 true 不能覆盖未解决关键冲突。报告以已核验 Claim 回答这些问题，问题记录用于组织和追溯而非冒充事实。

## 时间、修订与实体变更

`recorded_at` 是记录时间，`valid_from`／`valid_until` 是现实适用时间；`collected_at` 是获取时间，`published_at` 是原始发布日期，互不替代。非空时间必须用带时区的 ISO 8601；只有日期可确定时保留实际精度在适用条件中，时间字段留 null 并说明，不编午夜时间。未知起止时间为 null 且 validity.basis 说明依据或缺口；非空区间起点不得晚于终点。

同一 ID 修改追加新 revision，`supersedes` 精确指向旧版；不得原地改旧事实。实体合并保留所有旧 ID，用新版本的 superseded_by 和 entity_merge 事件记录依据、归属及受影响项；拆分分配新 ID，逐条处理关系、证据和结论，不能复制成两个都正确的主体。无法归属的材料保持未决。变更后重新核查相应覆盖与报告，旧快照不改。

每个版本目录是一份完整可解析的检查点快照，含其所需记录版本与原件或可核验原始引用；避免恢复必须递归读几十个旧目录。可以按需复制小型台账，原始大文件仅在权限允许且引用可稳定验证时复用；不把用户资料复制到公共仓库。

## 写入、预算与检查点

主控是正式台账的唯一写入者；Worker 返回独立结果和实际操作计数，主控检查后纳入，不能并发改同一文件。每条记录先写临时文件并检查，生成新版本目录后最后原子写入 manifest，manifest 列出九个台账文件的实际 SHA-256 与记录数。保存完整原件时同样核对哈希；不能保存正文时保留可复核链接、原文片段及定位，无法再访问要说明。

至少在每 5 个外部研究操作或每 10 条新增材料（先达到者）、范围／实体修订、补研及 QA 结束、上下文即将切换和导出报告前建立检查点。单次操作开始前记录唯一 operation_key 与预算预留，完成后追加结果事件并结算。每个 operation_key 的最终用量只结算一次；成功和失败均计耗用，结果不明保守计入且禁止盲目重放。事件不可通过重复记账／忽略失败制造剩余预算。

manifest 保存原始问题、目的、范围版本、当前阶段、偏好及默认依据、质量策略版本、模式、预算上限／已耗／预留、QA 覆盖摘要和下一步。计数必须能与事件及覆盖文件对账，不一致先停相关执行并修复，不能只相信摘要。记录不足时说明无法确认恢复状态，不伪造“全部完成”。

缺可写位置或写入失败时保留已核实材料并报告阻碍；短答可明确为对话内临时结果，但不能声称已建立台账、可恢复或已完成 PDF／PPT 文件交付。用户未给路径时使用当前工作区 research 目录，不再要求建立业务项目。

## 长任务上下文与恢复

恢复顺序：读 manifest → 验证文件哈希和行数 → 读未完成 task 与最近 checkpoint／失败事件 → 依本次问题加载指定 entity／claim／evidence／source。不要把全部原文、全部历史和整份聊天重复塞入上下文。

每批默认最多 20 条记录或 16,000 个字符，先到为止；大记录单独读取并保留边界，不能截断原文后宣称已读完整。用 ID、维度和任务状态筛选；需要时继续分批读取。便捷摘要只作导航，不是事实来源；不得用摘要代替原文定位、有效版本及反证。预算账本保留全局汇总，按操作标识读取局部细节。

读取到半行、损坏记录、哈希失配、未知 Schema 版本或悬空引用时，保留原文件并记录恢复诊断；从最后完整检查点构建新修订，只重做受影响部分，不静默截尾或修补旧档。running 状态不等于成功，也不等于应该重试；先核 operation_key 的结果、外部可观察状态与剩余预算，结果不明按故障规则处理。

## 报告绑定台账，不互相转写

1. 先锁定一个已经核查的输入快照，实际计算 manifest 字节的 SHA-256；后续不得修改该快照。重要事实／判断必须已有可接纳的 Claim，支持链、时效和冲突通过 [质量策略](research-quality.md)。
2. PDF 和 PPT **各自读取同一 input_snapshot_id 和 input_manifest_sha256**，按角色与详略组织内容；不得 PDF→PPT、PPT→PDF 或只读聊天摘要转写。两者可以布局不同，但数字、条件、结论与关键缺口一致。
3. 每段事实、表格行、图形数据或幻灯片的事实／判断位置登记 claim_refs；背景解释登记 background_refs 并显式标注，不计事实覆盖；格式见 [背景知识规则](source-quality.md)。纯标题可空，限制说明也应对应台账缺口或有引用的限制结论。发现正文中台账没有的新事实必须退回核查，不可只补一个装饰性编号。
4. 报告产物的元信息、引用映射和导出检查结果写入 reports/reports.jsonl，使用相同记录 Schema 的 report 类型；它不参与输入 manifest 哈希，避免自引用。文件实际生成后计算哈希，分别检查内容、引用、渲染与跨格式一致性，再标 qa_passed。
5. 证据或 Claim 更新就生成新快照和新报告修订；若仅更改布局可继续引用原快照，但保存新的报告文件／报告记录，不覆盖旧产物。PDF 已生成、PPT 尚未生成时也不能修改旧输入台账。

## 校验层次

先对每条 JSONL 及 manifest 做 Schema 检查，再检查：ID＋revision 唯一；supersedes 链无环且同 ID 递增；引用存在且类型匹配；来源副本一致；文件类型匹配；日期／区间合法；来源独立性和支持语义成立；coverage 的 covered 引用合格 Claim；预算与事件一致；snapshot 哈希正确。支持状态 Claim 至少有一条支持证据，关键判断满足两个独立来源组，不能由空数组通过语义守卫。

最后核报告正文和映射双向完整性、文件哈希、输入版本一致性；JSON 可解析不等于论证成立。当前仓库交付的是格式和执行协议，不声称这些跨记录守卫已有独立程序实现。新增／修改 Schema 需补结构正反例及对应语义验收记录。仓库提供 [结构正反例](../schemas/validation-cases.json)，全部为明确标记的合成测试数据，不可当作真实研究材料；运行时逐例按指定 Schema 校验，并启用 date-time 格式检查。
