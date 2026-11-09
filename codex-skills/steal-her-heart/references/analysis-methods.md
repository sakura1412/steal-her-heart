# Analysis Method Registry

当前逐项覆盖见 [验证记录](../validation/README.md)，验证元信息单独记录，不改变方法定义版本。

方法定义独立于研究流程，机器可读单源为 [analysis-method-registry.yaml](analysis-method-registry.yaml)。目录提供 Codex 可读的执行契约，不是已部署算法库，也不证明方法已在真实案例验收。方法的专业行业变体经 IndustryPack 引用或扩展，不在核心 Prompt 写死。

## 选择时点与触发

用户问题 → 动态框架与候选输入需求 → 取证及 Research QA → Planner 根据当前目标、数据和决策问题确定方法 → 必要的比较准入 → Analysis 执行 → 新结论核查 → 报告。

前期方法只是候选，不能成为固定流程。每次选用记录 question_id、method_id／version、为何适用、可用输入及不足、预期回答和未采用理由。选择最少且足够的方法；同一问题不必堆叠所有模型。没有适用方法可以直接做证据综合或定义明确的任务内方法，但必须同样说明输入、步骤和输出边界，不能隐藏自由发挥。

消费品牌渠道研究可以在有适用数据时选择价值链、客户旅程或标杆分析，不因为行业包中出现 API 条目就分析 API。不适用方法记 skipped；方法缺少必需输入记 blocked，并给具体补证问题。不能换一个容易完成的方法掩盖原目标缺口。

## 从研究问题选方法

| 常见问题 | 适用方法 | 最终应看见的结果 |
|---|---|---|
| 哪些内部优劣和外部变化影响战略？ | SWOT（`swot`） | 指定主体的四类因素、证据和行动含义 |
| 哪些宏观变化会影响本行业？ | PEST（`pest`） | 政治、经济、社会、技术因素及影响路径；要单列法律和环境时可选 PESTLE |
| 行业利润空间受到谁的挤压？ | 波特五力（`five_forces`） | 现有竞争、买卖双方议价、替代和进入威胁的逐项依据 |
| 用户完成任务时在哪里遇到摩擦？ | 用户旅程地图（`customer_journey`） | 按阶段对齐目标、接触点、反馈、障碍与证据 |
| 各家在同一项任务上能做到哪一步？ | 功能对比矩阵（`feature_matrix`） | 同口径能力、适用版本、支持状态与限制 |
| 距目标能力还有多远，哪些差距值得投入？ | GAP 分析（`capability_gap_analysis`） | 当前能力、目标基线、差距及其影响；用客户需求检验差距是否构成机会 |

**产品规划可进一步选择四种方法。** 评估业务组合用波士顿矩阵（`bcg_matrix`）。判断哪类需求会影响满意度用 Kano（`kano`）。在既定目标下比较多个提案的投入顺序用 RICE（`rice`）。解释一家企业如何创造、交付和获取价值用商业模式画布（`business_model_canvas`）。方法定义参考[BCG 对增长份额矩阵的说明](https://www.bcg.com/about/overview/our-history/growth-share-matrix)、[Kano 等人的原始研究](https://www.jstage.jst.go.jp/article/quality/14/2/14_KJ00002952366/_article/-char/en)和[Intercom 的 RICE 说明](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers/)。

例如，用户问“竞品有离线保存，我们该不该补？”时，功能矩阵先确认离线可用的操作和版本，GAP 再对照目标用户需要完成的任务。只有配对问卷时才能用 Kano 做正式需求分类。要把它与其他提案用 RICE 排序，还需相同时间窗的触达、影响、信心依据和投入估算。证据只够回答前两步时，交付已核实的能力差距，并写明下一步要验证的客户需求。

## 共同输入与输出约定

同次研究可为不同对象选择不同方法。先声明方法回答哪个对象的哪个问题。跨对象比较只使用共同定义和口径匹配的证据。例如，渠道伙伴决定甲的交付时，可以对甲做价值链分析；乙以自营服务交付时，可用用户旅程检查它的交接体验。最后围绕同一客户任务比较等待与恢复结果，各方法自身的评分留在各自适用范围内。对行动建议保留“建议 → 竞争判断 → Claim → Evidence → Source”的引用路径，明确什么新证据会让建议改变。

required_inputs 指所选方法确实执行所需材料，不表示用户必须亲自填表。evidence_records 是本次核验、带实体／使用端／地区／时期条件及来源定位的证据；comparison_scope 是当前有授权的比较范围；comparable_group 是通过 [可比性检查](comparison.md) 的组记录；这些公共含义应用于所有条目。

方法只能消费匹配版本的输入；缺失、冲突、失败和不适用分别保留。个别问题可先完成，但整体方法未满足完成条件时只能输出有界结果，不得标 completed。

统一结果记录 method_id／version、question_id、scope_version、输入和证据引用、采用步骤、计算／判断依据、claim_refs、limitations、status（completed／limited／blocked／skipped）。completed 只表示方法达到其完成条件，不替代完整 Research QA、Claim 支持核查或 Report QA。

所有事实遵守八条证据铁律；推断写明推理链、反例和限制。数值计算用实际可用计算工具复算，保留公式版本、输入、单位、权重及敏感性；不得仅让 LLM 输出一个分数。对外结论不将假设包装成事实。

## 比较、计算与解释分工

Comparison Engine 负责比较资格、定义映射、单位和情景标准化、缺失处理、权重与可视化结构；方法负责用合格输入回答问题。Feature Matrix、Price Analysis、Benchmarking、Capability Gap 等不得绕过比较准入，也不重复维护另一套缺失值或评分规则。

Analysis 不直接联网补缺、不改变范围或权重来追求预期结论。发现不足交 Planner；已经核实的独立部分可形成有限结果。Reporter 不补新模型或新策略，方法名不是结果可信度标签。

五力的通用类别参考 [Harvard Business School 的说明](https://www.isc.hbs.edu/strategy/business-strategy/Pages/the-five-forces.aspx)，本次读取日期 2026-11-04。目录其余规则是本项目为这些方法设定的取证、执行与交付约束，不声称是某学术标准的完整实现。TAM／SAM／SOM 定义与计算守卫见 [市场研究](market-intelligence.md)。

## 方法管理及行业扩展

同一 method_id＋version 的定义不可变。IndustryPack 的 Analysis Method 部分优先引用精确方法版本与适用条件；只添加选择提示时不用复制方法全文。确需改变输入、公式或步骤时以有命名空间的变体登记，并记录基方法与差异，不在同一 ID 下偷偷改规则。

包版本与方法版本分别记录，依赖可解析且无循环。方法变更重查受影响结论；旧报告保留原引用。结构校验、确定性计算检查和语义／行为验收分开，不因 YAML 可读就宣布方法有效。专业内容可以扩展，但不执行远端方法代码、不授权新工具或强制新研究范围。
