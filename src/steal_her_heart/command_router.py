"""Route competitor research requests with optional own-product context."""

import re
from typing import Annotated, Literal, Optional

from pydantic import Field, model_validator

from .project_initialization import require_project
from .schemas import (
    Contract,
    Identifier,
    ProjectContext,
    ReaderProfile,
    TaskRequest,
    Text,
    ToolContext,
)
from .tools import Tool

TargetName = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
Action = Literal["analyze", "battlecard", "brief"]
Period = Literal["weekly", "monthly"]


class CommandIntent(Contract):
    original_input: Text
    status: Literal["parsed", "needs_clarification", "unsupported"]
    action: Optional[Action] = None
    competitor: Optional[TargetName] = None
    period: Optional[Period] = None
    audiences: tuple[Identifier, ...] = ()
    question: Optional[Text] = None

    @model_validator(mode="after")
    def check_intent(self) -> "CommandIntent":
        if self.status == "parsed":
            if self.action is None or self.question is not None:
                raise ValueError("Parsed intents need one action and no unanswered question")
            if self.action in {"analyze", "battlecard"} and not self.competitor:
                raise ValueError("A single-competitor action needs a target")
            if self.action == "brief" and self.period is None:
                raise ValueError("A periodic brief needs an explicit period")
        elif not self.question:
            raise ValueError("Unresolved requests must explain what is missing")
        if self.action == "brief":
            if self.competitor is not None:
                raise ValueError("Periodic briefs are not a single-competitor action")
        elif self.period is not None:
            raise ValueError("Only periodic briefs accept a period")
        if len(set(self.audiences)) != len(self.audiences):
            raise ValueError("Reader IDs must be unique")
        return self


class CommandResponse(Contract):
    intent: CommandIntent
    execution: Literal[
        "pending_module", "needs_clarification", "unsupported", "initialization_required"
    ]
    message: Text
    initialization_question: Optional[Text] = None
    configured_readers: tuple[ReaderProfile, ...] = ()
    reader_requirements: Optional[Text] = None
    output_path: Literal[None] = None

    @model_validator(mode="after")
    def check_execution(self) -> "CommandResponse":
        expected = {"pending_module"} if self.intent.status == "parsed" else {self.intent.status}
        if self.intent.action is not None:
            expected.add("initialization_required")
        if self.execution not in expected:
            raise ValueError("Parsing cannot be reported as successful research or generation")
        if (self.execution == "initialization_required") != (
            self.initialization_question is not None
        ):
            raise ValueError("Initialization gating must include the next interview question")
        return self


def _intent(
    original: str,
    action: Optional[Action],
    target: Optional[str] = None,
    period: Optional[Period] = None,
    question: Optional[str] = None,
) -> CommandIntent:
    return CommandIntent(
        original_input=original,
        action=action,
        competitor=target,
        period=period,
        status="unsupported"
        if action is None
        else ("needs_clarification" if question else "parsed"),
        question=question,
    )


def _single(original: str, action: Action, target: str) -> CommandIntent:
    target = target.strip()
    pairs = {'"': '"', "“": "”", "《": "》", "'": "'"}
    if len(target) >= 2 and target[0] in pairs and target[-1] == pairs[target[0]]:
        target = target[1:-1].strip()
    if (
        not target
        or len(target) > 200
        or re.search(r"[\n\r;；]|、|，|,|然后|同时|并生成|顺便|&&|\|\||`|\$\(", target)
    ):
        return _intent(original, action, question="请指定单个竞品的名称（最多200字）。")
    return _intent(original, action, target=target)


def parse_request(request: TaskRequest) -> CommandIntent:
    original = request.user_input
    text = original.strip().rstrip("。！!？?")
    if text.startswith("/"):
        # Whitespace between command and target can also be tabs or full-width spaces.
        parts = re.split(r"\s+", text, maxsplit=1)
        command, tail = parts[0].lower(), parts[1] if len(parts) == 2 else ""
        if command in {"/analyze", "/battlecard"}:
            return _single(original, command[1:], tail)
        if command == "/brief":
            periods = {
                "周报": "weekly",
                "weekly": "weekly",
                "月报": "monthly",
                "monthly": "monthly",
            }
            period = periods.get(tail.strip().lower())
            return _intent(
                original,
                "brief",
                period=period,
                question=None if period else "需要生成周报还是月报？请指定 周报 或 月报。",
            )
        return _intent(original, None, question="支持 /analyze、/battlecard、/brief 三种研究命令。")
    text = re.sub(r"^(?:请帮我|帮我|请|麻烦|please)\s*", "", text, flags=re.I)
    # Compound or qualified requests are not silently reduced to the first action.
    if re.search(r"[\n\r;；]|然后|同时|并且|并生成|顺便|不要|不生成", text):
        return _intent(original, None, question="请一次提交一个明确任务，复杂限制需补充确认。")
    report = re.fullmatch(
        r"(?:生成|制作|做|汇总|输出)?\s*(?:一份)?\s*(?:面向四类(?:角色|读者)的)?"
        r"(?:竞品)?(周报|月报|weekly report|monthly report|weekly brief|monthly brief)",
        text,
        re.I,
    )
    if report:
        return _intent(
            original,
            "brief",
            period=(
                "weekly"
                if report[1].lower() in {"周报", "weekly report", "weekly brief"}
                else "monthly"
            ),
        )
    if re.fullmatch(r"(?:生成|制作|做|输出)?(?:一份)?(?:竞品)?简报", text):
        return _intent(original, "brief", question="需要生成周报还是月报？")
    card = re.fullmatch(
        r"(?:生成|制作|做)(?:一张|一份)?\s*(.+?)\s*的?\s*"
        r"(?:销售用的|销售)?(?:battlecard|一页纸)",
        text,
        re.I,
    ) or re.fullmatch(
        r"(?:给|为)\s*(.+?)\s*(?:生成|制作|做)(?:一张|一份)?\s*"
        r"(?:销售用的|销售)?(?:battlecard|一页纸)",
        text,
        re.I,
    )
    if card:
        return _single(original, "battlecard", card[1])
    analyze = re.fullmatch(
        r"(?:采集并更新|收集并更新|分析|研究|采集|更新)\s*(.+?)(?:的)?(?:竞品)?(?:档案|资料)?",
        text,
        re.I,
    )
    if analyze:
        return _single(original, "analyze", analyze[1])
    return _intent(
        original, None, question="请指定采集单个竞品、生成销售一页纸，或生成周报／月报。"
    )


def route_request(
    request: TaskRequest,
    project: Optional[ProjectContext] = None,
    initialization_question: Optional[str] = None,
) -> CommandResponse:
    intent = parse_request(request)
    if intent.status != "parsed":
        return CommandResponse(intent=intent, execution=intent.status, message=intent.question)
    # Only a caller that explicitly selected an incomplete project requests this gate.
    # An absent optional context must never start an own-product interview by itself.
    if project is None and initialization_question is not None:
        return CommandResponse(
            intent=intent,
            execution="initialization_required",
            message="所选项目配置尚未就绪；可补齐该项目，或不带项目参数直接研究竞品。",
            initialization_question=initialization_question or "请补齐所选项目配置。",
        )
    if project is not None:
        project = require_project(project)
    readers = project.readers if project and intent.action in {"brief", "battlecard"} else ()
    values = intent.model_dump()
    values["audiences"] = tuple(reader.id for reader in readers)
    intent = CommandIntent.model_validate(values)
    missing = {
        "analyze": "已识别研究对象，无需我方产品背景；采集、更新与研究执行尚未实现。",
        "battlecard": (
            "已识别销售一页纸请求；可先整理竞品画像，"
            "我方优势和回应需相关背景及双方证据。采集和报告生成尚未接入。"
        ),
        "brief": (
            "已识别周报／月报请求，无需我方产品背景；"
            "生成前需明确研究资产、对象及时间范围。资产查询和报告生成尚未接入。"
        ),
    }
    return CommandResponse(
        intent=intent,
        execution="pending_module",
        message=missing[intent.action],
        configured_readers=readers,
        reader_requirements=project.readers_and_priorities if project else None,
    )


class RequestRoutingTool(Tool):
    name = "route_request"
    description = "Parse research requests with optional project context; no research execution"
    input_model = TaskRequest
    output_model = CommandResponse
    required_capabilities = frozenset({"request_routing"})

    def __init__(
        self,
        project: Optional[ProjectContext] = None,
        initialization_question: Optional[str] = None,
    ) -> None:
        self.project = project
        self.initialization_question = initialization_question

    async def run(self, arguments: TaskRequest, context: ToolContext) -> CommandResponse:
        return route_request(arguments, self.project, self.initialization_question)
