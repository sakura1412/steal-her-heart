"""Reader contracts and evidence-bound battlecards; no report generation."""

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .schemas import Analysis, Contract, Identifier, Text


class Audience(str, Enum):
    PRODUCT_MANAGER = "product_manager"
    SALES_LEADER = "sales_leader"
    SALES_REP = "sales_rep"
    OPERATIONS = "operations"


class ReaderViewSpec(Contract):
    audience: Identifier
    title: Text
    sections: tuple[Text, ...] = Field(min_length=1)
    layout: Literal["report", "one_page"] = "report"
    priority: Literal["standard", "core"] = "standard"


READER_VIEWS = (
    ReaderViewSpec(
        audience=Audience.PRODUCT_MANAGER,
        title="产品经理决策视图",
        sections=(
            "能力对比矩阵（我方有／没有与领先／落后分开）",
            "差距分析与机会点",
            "AI等相关趋势的跟进情况",
            "路线图建议",
        ),
    ),
    ReaderViewSpec(
        audience=Audience.SALES_LEADER,
        title="销售领导决策视图",
        sections=(
            "市场格局与竞品近期动向",
            "定价对比与我方价格空间",
            "赢单／丢单原因归纳",
            "重点竞争对象与应避开的场景",
        ),
    ),
    ReaderViewSpec(
        audience=Audience.SALES_REP,
        title="一页 Battlecard",
        layout="one_page",
        priority="core",
        sections=(
            "对方三个主要卖点、客户可能怎么说、我们怎么回应",
            "我方三个可证明的差异化优势",
            "客户需求与适配性探测问题",
            "不能说的话及安全替代表达",
        ),
    ),
    ReaderViewSpec(
        audience=Audience.OPERATIONS,
        title="运营行动视图",
        sections=(
            "获客渠道、内容与活动打法",
            "促销和定价策略变化",
            "新手引导与客户成功动作",
            "可借鉴的增长实验",
        ),
    ),
)


class EvidenceReference(Contract):
    evidence_id: Identifier


class AnalysisReference(Contract):
    claim_id: Identifier


class OpponentSellingPoint(Contract):
    point: EvidenceReference
    customer_expression: Text
    expression_kind: Literal["hypothetical", "observed"] = "hypothetical"
    observation_refs: tuple[EvidenceReference, ...] = ()
    response: AnalysisReference

    @model_validator(mode="after")
    def check_observation(self) -> "OpponentSellingPoint":
        if self.expression_kind == "observed" and not self.observation_refs:
            raise ValueError("Observed customer language needs recorded evidence")
        if self.expression_kind == "hypothetical" and self.observation_refs:
            raise ValueError("Hypothetical language must not masquerade as observed testimony")
        return self


class DiscoveryQuestion(Contract):
    question: Text
    rationale: AnalysisReference


class DoNotSay(Contract):
    prohibited: Text
    reason: Text
    safer_alternative: Text
    basis: Literal["evidence_gap", "contradicted", "scope_mismatch", "stale_information"]
    evidence_refs: tuple[EvidenceReference, ...] = ()

    @model_validator(mode="after")
    def check_basis(self) -> "DoNotSay":
        if self.basis == "contradicted" and not self.evidence_refs:
            raise ValueError("A contradiction must reference the contradictory evidence")
        return self


class Battlecard(Contract):
    schema_version: Literal[1] = 1
    audience: Identifier = "sales_rep"
    layout_target: Literal["one_page"] = "one_page"
    status: Literal["draft", "ready"] = "draft"
    competitor: Text
    own_product: Optional[Text] = None
    as_of: date
    analysis: Optional[Analysis] = None
    opponent_points: tuple[OpponentSellingPoint, ...] = Field(default=(), max_length=3)
    our_advantages: tuple[AnalysisReference, ...] = Field(default=(), max_length=3)
    discovery_questions: tuple[DiscoveryQuestion, ...] = ()
    do_not_say: tuple[DoNotSay, ...] = ()
    gaps: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def check_content(self) -> "Battlecard":
        facts = {fact.id: fact for fact in self.analysis.facts} if self.analysis else {}
        claims = {claim.id: claim for claim in self.analysis.claims} if self.analysis else {}
        if self.analysis and len(claims) != len(self.analysis.claims):
            raise ValueError("Shared analysis claim IDs must be unambiguous")
        if self.own_product == self.competitor:
            raise ValueError("The reference product and competitor must differ")
        evidence_refs = [point.point for point in self.opponent_points]
        evidence_refs += [ref for point in self.opponent_points for ref in point.observation_refs]
        evidence_refs += [ref for item in self.do_not_say for ref in item.evidence_refs]
        claim_refs = list(self.our_advantages)
        claim_refs += [point.response for point in self.opponent_points]
        claim_refs += [question.rationale for question in self.discovery_questions]
        if any(ref.evidence_id not in facts for ref in evidence_refs):
            raise ValueError("Battlecard references unknown evidence")
        if any(ref.claim_id not in claims for ref in claim_refs):
            raise ValueError("Battlecard references unknown analysis")
        for point in self.opponent_points:
            if facts[point.point.evidence_id].competitor != self.competitor:
                raise ValueError("Opponent selling points need evidence about this competitor")
        if len({point.point.evidence_id for point in self.opponent_points}) != len(
            self.opponent_points
        ) or len({ref.claim_id for ref in self.our_advantages}) != len(self.our_advantages):
            raise ValueError("Repeated references cannot fill the three-point quota")
        if self.our_advantages and not self.own_product:
            raise ValueError("Our advantages require reference-product context")
        for ref in self.our_advantages:
            subjects = {facts[eid].competitor for eid in claims[ref.claim_id].evidence_ids}
            if not {self.competitor, self.own_product}.issubset(subjects):
                raise ValueError("Advantages need evidence about both products")
        if self.status == "draft" and not self.gaps:
            raise ValueError("Incomplete drafts must disclose concrete gaps")
        if self.status == "ready" and (
            not self.analysis
            or not self.own_product
            or self.gaps
            or len(self.opponent_points) != 3
            or len(self.our_advantages) != 3
            or not self.discovery_questions
            or not self.do_not_say
        ):
            raise ValueError(
                "Ready battlecards need all sections and three supported points per side"
            )
        return self
