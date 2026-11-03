# 独立研究项目

每个产品使用一个独立子目录，例如 `projects/my-product/`。目录名是本地项目标识，可使用中文、字母、数字、下划线和连字符；不代表行业白名单。

```text
projects/<项目标识>/
  profile.md                  产品背景、市场、目的及读者配置
  dimensions.md               初始化维度建议／预确认，最终绑定名单后才锁定
  dimensions.proposal.json    阶段2结构化提案（设计后生成）
  dimensions.lock.json        阶段2确认记录（确认后生成）
  .state/project.sqlite3      阶段0访谈进度
  .state/memory.sqlite3       本产品研究任务及事件
```

Codex 聊天使用 [初始化指令](../commands/init-project.md)：取得实际名称后，每轮2～3题访谈；完成 profile 后推荐并确认维度，再联网发现和确认四类候选。答不上来时给出选项，不把建议自动当答案，不创建名为 `$ARGUMENTS` 的项目。入口技能见 [competitor-research](../skills/competitor-research/SKILL.md)。

Python CLI 仍通过全局 `--project-name <项目标识>` 与 `/init-project --interactive` 逐题保存，default 仅为该 CLI 的历史默认值。研究入口读取 profile.md 和 dimensions.md；维度预确认不意味着已批准采集。现有 review API 仍须在名单批准后生成最终提案与锁，尚无聊天预确认的自动迁移；真实采集、核查、分析和报告执行仍待接入。

新 profile.md 使用 [标准可读模板](../templates/profile.md)，可直接填写业务字段和读者表，业务内容不另外藏在 JSON 中。空模板、注释示例或缺少核心内容时仍未就绪。程序把整个 profile 文件摘要绑定到任务上下文，变更后不能静默恢复旧任务或复用旧批准；已有手工编辑不会被初始化覆盖。

模板包含名称与一句话描述、多行业／业态、目标客群、市场范围、优势短板、价格区间、分析目的，以及每位读者的关注点与期望格式、可空竞品名单和特别说明。价格与特别说明可留空；未填写不代表免费或没有约束，不得由 Agent 猜测补全。

每份分析依据本次检索，结尾回答“对我们意味着什么、建议怎么做”。事实、官方宣称、第三方／用户反馈和推断分开，多角色产出共用事实资产。框架规则见仓库根目录 AGENTS.md。

目录中的访谈与背景可能含内部信息；本工具不会自动提交或上传项目资料。旧 `--project <SQLite路径>` 仅保留兼容入口，不会自动迁移成项目文件布局。

旧 JSON profile 继续走兼容读取，原内容不会自动重写；原始旧空稿可在访谈完成后生成新 Markdown。旧9题记录缺少价格区间、特别说明或读者格式时须保留缺口，不等于完成新版11题。不要把新 Markdown 与旧业务 JSON 混成两个互相矛盾的配置源。
