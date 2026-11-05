# Analysis Method Registry

当前逐项覆盖见 [验证记录](../validation/README.md)，验证元信息单独记录，不改变方法定义版本。

方法定义独立于研究流程，机器可读单源为 [analysis-method-registry.yaml](analysis-method-registry.yaml)。目录提供 Codex 可读的执行契约，不是已部署算法库，也不证明方法已在真实案例验收。方法的专业行业变体经 IndustryPack 引用或扩展，不在核心 Prompt 写死。

## 选择时点与触发

用户问题 → 动态框架与候选输入需求 → 取证及 Research QA → Planner 根据当前目标、数据和决策问题确定方法 → 必要的比较准入 → Analysis 执行 → 新结论核查 → 报告。

前期方法只是候选，不能成为固定流程。每次选用记录 question_id、method_id／version、为何适用、可用输入及不足、预期回答和未采用理由。选择最少且足够的方法；同一问题不必堆叠所有模型。没有适用方法可以直接做证据综合或定义明确的任务内方法，但必须同样说明输入、步骤和输出边界，不能隐藏自由发挥。

消费品牌渠道研究可以在有适用数据时选择价值链、客户旅程或标杆分析，不因为行业包中出现 API 条目就分析 API。不适用方法记 skipped；方法缺少必需输入记 blocked，并给具体补证问题。不能换一个容易完成的方法掩盖原目标缺口。

## 共同输入与输出约定

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
