# Competitive Intelligence Subject

研究请求以竞争情报研究对象 Subject 为入口，不要求一定提供某个竞品名称。Subject 是当前研究任务的抽象，Entity 是被研究的现实对象；定价、渠道和客户需求问题不是新的公司或产品实体。

## 十类任务

| 类型 | 目标 |
|---|---|
| Product Intelligence | 产品价值、能力与适用边界 |
| Company Intelligence | 企业业务、竞争优势及其依据 |
| Brand Intelligence | 品牌定位与竞争关系 |
| Service Intelligence | 服务内容、交付与体验 |
| Technology Intelligence | 技术路线、能力、成熟度与适用约束 |
| Pricing Intelligence | 价格、收费结构与策略 |
| Channel Intelligence | 销售／分发路径、覆盖与渠道策略 |
| Market Intelligence | 市场结构、规模、竞争格局与机会 |
| Industry Intelligence | 行业环境、产业链与变化 |
| Customer Intelligence | 客户选择、需求变化和行为依据 |

类型可多选，不能根据名称强行归一；它们是同一个 Agent 的任务标签，不是十个独立代理。技术路线或客户群体可按实体扩展规则建立有定义的对象，不能猜成同名企业。客户研究优先群体与公开／授权材料，不推断个人敏感属性。

## 识别与范围

保留原始请求，记录 subject_id、类型、决策问题、实体或待解析描述、地区／时期／使用端、比较或市场边界、读者、授权依据、未知项和 scope_version。缺省不伪造用户答案。报告请求先按 [顺序访谈](report-delivery.md) 确认角色和类型；其他缺口只有影响本次解释时才澄清。

只有请求连主题都没有时才问研究什么；“分析某行业市场格局”已经给了主题，不再追问竞品名。名称请求按上下文确定公司、品牌、产品等层级，必要时消歧。没有我方背景不阻断一般研究，但不能编我方优势或策略。

同一主题可以同时涉及多个任务类型：定价与渠道研究共享对象和事实，但分别定义问题。跨类型不重复采集，也不把渠道数据自动解释为市场份额。用户直接要求市场研究已授权相应主题范围；只给产品名时不能自动扩大为全市场采集。市场边界含糊且影响结论时只问必要范围。

## 路由与交付

通用引擎按问题选择 IndustryPack、Connector Adapter 和动态维度；完成取证 QA 后由 Planner 选择分析方法。涉及比较先按 [Comparison Engine](comparison.md) 建可比组；市场任务读 [Market Intelligence](market-intelligence.md)，具体方法读 [Analysis Method Registry](analysis-methods.md)。

输出形式由问题和读者决定，可以是事实画像、对比、情景分析、市场结构或简报，不强制所有模型／所有模块。新增主题或类型先分析对实体、框架、取证和结论的影响，保留旧版本；题目改写不表示新增授权。
