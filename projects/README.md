# 可选研究项目

默认 `/analyze 竞品名` 不需要本目录、项目名或我方产品背景，见 [单对象分析](../commands/analyze.md)。仅在用户需要长期跟踪、固定读者／维度，或反复进行我方对照时选择项目模式。

每个可选项目用一个独立子目录，例如 `projects/my-product/`。目录名是本地标识，不代表行业白名单。只有显式指定项目时才读取其档案；不会因存在一个未完成的默认项目而拦截独立研究。

```text
projects/<项目标识>/
  profile.md                  可选项目的产品、市场、目的及读者背景
  dimensions.md               项目维度预览，最终绑定名单后才锁定
  dimensions.proposal.json    Python项目评审提案（设计后生成）
  dimensions.lock.json        Python项目确认记录（确认后生成）
  .state/project.sqlite3      项目访谈进度
  .state/memory.sqlite3       绑定该项目的任务与事件
```

Codex 按 [项目初始化指令](../commands/init-project.md) 分批访谈；Python 使用 `--project-name <项目标识> /init-project --interactive` 逐题保存。profile 使用 [标准模板](../templates/profile.md)，业务内容在可读 Markdown 中保留一份，不另藏业务 JSON。空稿不代表档案完成，已有编辑不被初始化覆盖。

模板保留名称描述、多行业、客群、市场、优势短板、价格、目的、读者与期望格式、可空竞品名单及特别说明。它用于选择完整项目模式的用户，不是默认分析必须填写的问卷。Battlecard 所需的最少我方背景也可直接在聊天中补充，无须强制建项目。

项目模式保留维度／名单评审；Python review API 的摘要和锁仍绑定本项目背景，尚无完整聊天批准桥接。修改背景或维度后不静默复用旧批准，绑定任务恢复要求快照一致。单对象任务的 `project_context=None` 则在无项目模式下恢复，不能混用两种上下文。

旧 JSON profile 与旧九题访谈继续兼容读取，不覆盖；新增字段缺失保留缺口，原始空稿可完成访谈后升级。旧 `--project <SQLite路径>` 作为显式兼容入口，不自动迁移为目录项目。

研究报告默认可另存于 `research/<安全对象标识>/<运行标识>/`，仅在 Codex 真实生成后创建；Python 暂不输出报告。项目资料与本地研究产物默认不加入 Git。研究结论的来源和日期要求见 [AGENTS.md](../AGENTS.md)。
