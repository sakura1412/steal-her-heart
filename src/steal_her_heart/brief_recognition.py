"""Conservative offline request recognition, not open-domain model understanding."""

import re
from typing import Literal, Optional

from pydantic import model_validator

from .schemas import Contract, ResearchBrief, TaskRequest, Text, ToolContext
from .tools import Tool


class BriefQuestion(Contract):
    code: Literal["unsupported_request", "missing_entities", "entity_ambiguity"]
    question: Text


class BriefRecognition(Contract):
    status: Literal["ready", "needs_clarification"]
    recognizer_version: Literal["rules-v2"] = "rules-v2"
    brief: Optional[ResearchBrief] = None
    questions: tuple[BriefQuestion, ...] = ()
    limitations: tuple[Text, ...] = (
        "离线规则识别只覆盖明确的请求句式与本地别名；不验证产品身份或事实。",
    )

    @model_validator(mode="after")
    def check_status(self) -> "BriefRecognition":
        if self.status == "ready" and (self.brief is None or self.questions):
            raise ValueError("Ready recognition needs a brief and no blocking questions")
        if self.status == "needs_clarification" and (self.brief is not None or not self.questions):
            raise ValueError("Blocked recognition needs questions and cannot expose a final brief")
        return self


class EntityHint(Contract):
    """Trusted local routing hints, never research evidence."""

    alias: Text
    canonical_name: Text
    industry: Optional[Text] = None
    disambiguation_terms: tuple[Text, ...] = ()


DEFAULT_HINTS = (
    EntityHint(
        alias="cursor",
        canonical_name="Cursor",
        industry="AI编程工具",
        disambiguation_terms=("AI编程", "AI 编程", "编程工具", "代码编辑器", "code editor"),
    ),
    EntityHint(
        alias="claude",
        canonical_name="Claude",
        industry="AI助手",
        disambiguation_terms=("AI助手", "AI 助手", "anthropic", "聊天助手"),
    ),
    EntityHint(alias="claude code", canonical_name="Claude Code", industry="AI编程工具"),
    EntityHint(alias="飞书", canonical_name="飞书", industry="协同办公与知识管理"),
    EntityHint(alias="feishu", canonical_name="飞书", industry="协同办公与知识管理"),
    EntityHint(alias="notion", canonical_name="Notion", industry="协同办公与知识管理"),
    EntityHint(alias="confluence", canonical_name="Confluence", industry="协同办公与知识管理"),
)
DEFAULT_DIMENSIONS = ("定位与价值", "核心产品与服务", "价格与商业模式", "用户与使用场景")
DIMENSION_ALIASES = {
    "价格": "收费模式",
    "定价": "收费模式",
    "pricing": "收费模式",
    "price": "收费模式",
    "功能": "核心功能",
    "features": "核心功能",
    "部署": "部署方式",
    "deployment": "部署方式",
    "用户": "目标用户",
    "users": "目标用户",
    "ai": "AI能力",
    "ai能力": "AI能力",
    "ai功能": "AI能力",
}
SEPARATOR = re.compile(
    r"\s*(?:、|[，,]\s*(?:and\b|以及|和|与)?|以及|和|与|及|\band\b|\bversus\b|\bvs\b\.?)\s*",
    re.I,
)


def unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


class RuleBasedBriefRecognizer:
    """Extract only supported request syntax; preserve uncertainty rather than inventing it."""

    def __init__(self, hints: tuple[EntityHint, ...] = DEFAULT_HINTS) -> None:
        self._hints: dict[str, EntityHint] = {}
        for hint in hints:
            key = hint.alias.casefold()
            if key in self._hints:
                raise ValueError("Duplicate entity alias")
            self._hints[key] = hint

    async def understand(self, request: TaskRequest) -> BriefRecognition:
        request = TaskRequest.model_validate_json(request.model_dump_json())
        original = request.user_input
        text = original.strip().rstrip("。.!！?？")
        if re.search(
            r"不是|不要|不看|不比较|不分析|不研究|不关注|不包括|不包含|\bnot\b|\bexcept\b",
            text,
            re.I,
        ):
            return self._blocked(
                "unsupported_request",
                "离线识别暂不能可靠解析否定约束，请用肯定句列出本次研究对象与维度。",
            )
        match = re.match(
            r"^(?:(?:请|帮我|请帮我|麻烦|please)\s*)?"
            r"(?:分析|研究|比较|对比|compare\b|analyze\b|analyse\b|research\b)\s*(.+)$",
            text,
            re.I,
        )
        if match is None:
            return self._blocked("unsupported_request", "请明确研究对象，例如：比较 A、B 的价格。")
        body, quoted = self._mask_quotes(match.group(1).strip())
        assumptions: list[str] = []
        # An explicit industry prefix is scope supplied by the user, not inferred knowledge.
        industry_match = re.match(r"^(?:在)?([^的，,；;：:]{1,30}(?:行业|领域))(?:中)?的?\s*", body)
        explicit_industry = industry_match.group(1) if industry_match else None
        if industry_match:
            body = body[industry_match.end() :]
        else:
            for industry in sorted(
                {h.industry for h in self._hints.values() if h.industry}, key=len, reverse=True
            ):
                if body.casefold().startswith(industry.casefold()):
                    remainder = body[len(industry) :].lstrip()
                    if remainder:
                        explicit_industry, body = industry, remainder
                        break
        # The first qualifier terminates the entity clause. Unsupported suffixes ask for clarity.
        sections = re.split(
            r"(?:的|，\s*|,\s*)(?:重点(?:关注|看)?|主要(?:关注|看)?|只看|关注|维度[：:]?)"
            r"|\s+on\s+",
            body,
            maxsplit=1,
            flags=re.I,
        )
        entity_clause = sections[0]
        dimension_text = sections[1] if len(sections) == 2 else None
        if dimension_text is None and "的" in entity_clause:
            entity_clause, dimension_text = entity_clause.split("的", 1)
        entity_clause = entity_clause.strip()
        # Trailing English request qualifiers are not part of a product name.
        if dimension_text is None:
            english = re.split(r"\s+(?:for|in terms of)\s+", entity_clause, maxsplit=1, flags=re.I)
            if len(english) == 2:
                entity_clause, dimension_text = english
        parts = [quoted.get(p.strip(), p.strip()) for p in SEPARATOR.split(entity_clause)]
        if dimension_text:
            for marker, value in quoted.items():
                dimension_text = dimension_text.replace(marker, value)
        if dimension_text is not None and not dimension_text.strip():
            return self._blocked(
                "unsupported_request", "研究维度为空，请补充明确维度或省略维度子句。"
            )
        if not parts or len(parts) > 20 or any(not self._valid_entity(p) for p in parts):
            return self._blocked(
                "missing_entities", "请列出明确的产品或品牌名称，用顿号或逗号分隔。"
            )
        competitors: list[str] = []
        industries: list[str] = []
        questions: list[BriefQuestion] = []
        for name in parts:
            hint = self._hints.get(name.casefold())
            if hint is None:
                competitors.append(name)
                assumptions.append(f"“{name}”按用户提供的名称保留，产品身份尚未验证。")
                continue
            if hint.disambiguation_terms and not any(
                term.casefold() in original.casefold() for term in hint.disambiguation_terms
            ):
                questions.append(
                    BriefQuestion(
                        code="entity_ambiguity",
                        question=f"“{name}”是否指{hint.industry} {hint.canonical_name}？"
                        "如不是，请提供产品全称或所属行业。",
                    )
                )
                continue
            competitors.append(hint.canonical_name)
            if hint.industry:
                industries.append(hint.industry)
            if name != hint.canonical_name:
                assumptions.append(
                    f"根据本地别名表将“{name}”归一为“{hint.canonical_name}”，待研究核验。"
                )
        if questions:
            return BriefRecognition(status="needs_clarification", questions=tuple(questions))
        if explicit_industry:
            industry = explicit_industry
        elif len(set(industries)) == 1 and len(industries) == len(parts):
            industry = industries[0]
            assumptions.append("行业由本地词表推断，尚未通过来源验证。")
        else:
            industry = "待研究确认"
            assumptions.append("无法可靠确定统一行业；后续研究须核验，不强行归类。")
        dimensions = self._dimensions(dimension_text)
        if len(dimensions) > 20:
            return self._blocked(
                "unsupported_request", "单次识别最多支持20个研究维度，请缩小范围。"
            )
        if not dimensions:
            dimensions = DEFAULT_DIMENSIONS
            assumptions.append(
                "未明确研究维度，暂建议定位与价值、核心产品与服务、价格与商业模式、用户与使用场景，待首次计划确认。"
            )
        return BriefRecognition(
            status="ready",
            brief=ResearchBrief(
                topic="、".join(unique(competitors)) + "竞品研究",
                industry=industry,
                objective=original,
                competitors=unique(competitors),
                dimensions=dimensions,
                assumptions=unique(assumptions),
            ),
        )

    @staticmethod
    def _mask_quotes(text: str) -> tuple[str, dict[str, str]]:
        values: dict[str, str] = {}
        prefix = "SHHQUOTEDENTITY"
        while prefix in text:
            prefix += "X"

        def replace(match: re.Match) -> str:
            marker = f"{prefix}{len(values)}"
            values[marker] = next(value for value in match.groups() if value is not None)
            return marker

        return re.sub(r'"([^"\n]+)"|“([^”\n]+)”|《([^》\n]+)》|‘([^’\n]+)’', replace, text), values

    @staticmethod
    def _valid_entity(name: str) -> bool:
        return (
            bool(re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .&+()（）/\-]{0,79}", name))
            and not re.search(r"竞品|产品差异|一份|报告|忽略|执行|分析|研究|比较|对比", name)
            and name.casefold()
            not in {"产品", "品牌", "它", "这个", "这些", "competitors", "products"}
        )

    @staticmethod
    def _dimensions(text: Optional[str]) -> tuple[str, ...]:
        if not text or text.strip() in {"产品差异", "差异", "对比", "区别"}:
            return ()
        parts = SEPARATOR.split(text.strip().rstrip("。.!！"))
        # Retain explicit, unknown dimensions rather than silently dropping them.
        return unique([DIMENSION_ALIASES.get(p.casefold(), p) for p in parts if p.strip()])

    @staticmethod
    def _blocked(code: str, question: str) -> BriefRecognition:
        return BriefRecognition(
            status="needs_clarification", questions=(BriefQuestion(code=code, question=question),)
        )


class BriefRecognitionTool(Tool):
    name = "recognize_brief"
    description = "Offline ResearchBrief recognition; no network, planning or state transitions"
    input_model = TaskRequest
    output_model = BriefRecognition
    required_capabilities = frozenset({"brief_recognition"})

    def __init__(self, recognizer: Optional[RuleBasedBriefRecognizer] = None) -> None:
        self.recognizer = recognizer or RuleBasedBriefRecognizer()

    async def run(self, arguments: TaskRequest, context: ToolContext) -> BriefRecognition:
        return await self.recognizer.understand(arguments)
