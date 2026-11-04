"""Deterministic, user-facing boundaries shared by CLI and routing responses."""

from typing import Literal, Optional

from .schemas import ExecutionGuidance, MissingModule

PREVIEW_NOTICE = (
    "开发预览：当前仅支持初始化与工程验证，研究执行链路未就绪。"
    "独立工具用于诊断，不能自动生成有证据支撑的竞品报告。"
)
PATHS_NOTICE = (
    "Python CLI：项目档案、需求识别与独立工具验证；"
    "Codex Skill：依赖宿主实际联网工具的辅助研究路径，不自动同步 Python 任务或范围锁。"
)

_COMMON = (
    MissingModule(id="planner_execution", description="研究计划的实际执行"),
    MissingModule(id="research_orchestration", description="研究调度与已有搜索、抓取工具的串联"),
    MissingModule(id="semantic_extraction", description="自动语义证据提取"),
    MissingModule(id="evidence_validation", description="来源支持、时效、覆盖和冲突核查"),
    MissingModule(id="analysis_generation", description="有证据引用的分析生成"),
    MissingModule(id="report_generation", description="可读报告生成与产物验收"),
    MissingModule(id="llm_runtime", description="真实模型与运行时 Prompt 接入"),
)


def pending_guidance(
    action: Optional[Literal["analyze", "battlecard", "brief"]] = None,
    *,
    competitor: Optional[str] = None,
    period: Optional[Literal["weekly", "monthly"]] = None,
) -> ExecutionGuidance:
    """Explain a blocked request without turning it into an execution or retry."""
    modules = _COMMON
    codex_prompt = None
    if action == "analyze":
        summary = "已识别竞品分析请求，但 Python 研究链路未就绪；未采集资料或生成报告。"
        steps = (
            "需要现在开展辅助研究：把下方提示发到 Codex，使用实际可用的联网工具取证并核查；"
            "该路径不连接 Python 任务，也不保证自动完成报告。",
            "继续工程验证可查看 steal-her-heart recognize-brief --help；"
            "它只识别需求，不验证竞品事实。",
            "无需重新初始化；重复执行 analyze 或 resume 不会补齐缺失模块。",
        )
        if competitor:
            codex_prompt = f"$steal-her-heart {competitor}"
    elif action == "battlecard":
        summary = "已识别销售一页纸请求，但采集、双方对照与卡片生成未接通；未生成 Battlecard。"
        modules += (
            MissingModule(id="battlecard_view", description="销售卡片生成与一页可读版式验收"),
        )
        steps = (
            "需要现在准备卡片：可在 Codex 辅助研究对方，并提供我方产品、销售场景和可核验资料；"
            "由实际证据形成草稿并人工核查。",
            "没有我方背景时可先整理对方画像；我方优势、回应和赢输判断保留缺口，"
            "无需先填写完整项目访谈。",
            "Python 当前没有可生成卡片的后续命令；重新初始化或重试不会生成产物。",
        )
        if competitor:
            codex_prompt = (
                f"$steal-her-heart {competitor}\n"
                "请先说明开发预览与宿主工具边界，再辅助整理销售卡片草稿；"
                "我方对照只使用我提供的资料和实际证据，缺资料时列出缺口。"
            )
    elif action == "brief":
        title = {"weekly": "周报", "monthly": "月报"}.get(period, "周期报告")
        summary = f"已识别{title}请求，但研究资产查询与周期报告生成未接通；未生成报告。"
        modules += (
            MissingModule(id="research_asset_query", description="研究资产检索与本周期有效性检查"),
            MissingModule(id="periodic_reader_views", description="周期报告及配置读者视图生成"),
        )
        steps = (
            "先准备本周期的对象范围、具体起止日期、来源材料与获取日期；"
            "当前 Python 不会自动查找上次任务的证据或生成增量周报／月报。",
            "若现在需要草稿，可把这些材料交给 Codex 辅助整理并核查来源；"
            "这不代表 Python 资产库或周期报告已接通。",
            "已有项目档案可继续保留；无需重新初始化，重试 /brief 不会补齐缺失模块。",
        )
    else:
        summary = "任务已保存，但 Python 自动研究与报告链路未就绪；本次未执行研究或生成报告。"
        steps = (
            "需要现在研究：在 Codex 中使用 $steal-her-heart 并提供产品名称；"
            "先查看能力说明，只有实际可用的宿主工具与来源能支持辅助研究。",
            "继续工程验证可查看 steal-her-heart --help；"
            "独立 search、fetch 和摘录诊断不会自动推动研究任务。",
            "resume 只恢复已保存状态；无需重新初始化，重复恢复不会启动缺失的研究链。",
        )
    return ExecutionGuidance(
        summary=summary, missing_modules=modules, next_steps=steps, codex_prompt=codex_prompt
    )


def profile_saved_guidance() -> ExecutionGuidance:
    return ExecutionGuidance(
        reason="profile_saved_only",
        summary="项目档案已保存；这里只完成配置，不表示维度已推荐、候选已发现或研究已完成。",
        missing_modules=(
            MissingModule(
                id="project_planning_bridge", description="访谈到维度推荐、候选发现的执行衔接"
            ),
            MissingModule(
                id="confirmation_lock_bridge", description="聊天确认到 Python 最终锁的持久化桥接"
            ),
            *_COMMON,
        ),
        next_steps=(
            "Python 访谈到此结束，不会自动推荐维度、联网发现候选或开始采集；"
            "不必重复初始化，当前没有一条后续 CLI 命令能自动完成研究。",
            "需要辅助推进时，在当前 Codex 对话中明确选择项目模式并提供已有项目位置，"
            "请求基于实际工具推荐维度、发现候选；普通单对象研究不需要创建项目。",
            "聊天中的确认不等于 Python 已锁定；最终锁仍需通过现有评审 API 保存和回读，"
            "自动桥接尚未实现。",
        ),
    )


def render_guidance(guidance: ExecutionGuidance) -> str:
    """Human guidance goes to stderr; callers keep their existing JSON stdout."""
    lines = [guidance.summary]
    lines.append("尚未接通：" + "；".join(item.description for item in guidance.missing_modules))
    lines.extend(f"下一步 {number}：{step}" for number, step in enumerate(guidance.next_steps, 1))
    if guidance.codex_prompt:
        lines.extend(
            ("可复制到 Codex 的提示（辅助研究，不是 Python 执行命令）：", guidance.codex_prompt)
        )
    # A target name can contain controls even though JSON safely quotes ESC.
    # Preserve intentional line breaks without emitting terminal instructions.
    return "".join(
        f"\\x{ord(char):02x}"
        if char != "\n" and (ord(char) < 32 or 127 <= ord(char) < 160)
        else char
        for char in "\n".join(lines)
    )
