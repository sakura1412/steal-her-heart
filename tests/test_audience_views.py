from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from steal_her_heart.audience_views import (
    READER_VIEWS,
    AnalysisReference,
    Audience,
    Battlecard,
    DiscoveryQuestion,
    DoNotSay,
    EvidenceReference,
    OpponentSellingPoint,
    ReaderViewSpec,
)
from steal_her_heart.schemas import Analysis, AnalysisClaim, Evidence, Source


def analysis():
    source = Source(
        id="source",
        source_name="测试夹具",
        url="https://example.com/fixture",
        fetched_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        content_sha256="0" * 64,
        snapshot_ref="fixture.txt",
    )
    facts = tuple(
        Evidence(
            id=f"{side}-{n}",
            source=source,
            competitor=side,
            dimension=f"维度{n}",
            fact=f"{side} 的夹具事实{n}",
            quote=f"原文{n}",
            locator=f"行{n + 1}",
            confidence=0.7,
            confidence_reason="测试数据，不是真实研究",
        )
        for side in ("竞品", "我方")
        for n in range(3)
    )
    claims = tuple(
        AnalysisClaim(
            id=f"claim-{n}",
            kind="comparison",
            statement=f"特定场景下的比较{n}",
            evidence_ids=(f"竞品-{n}", f"我方-{n}"),
        )
        for n in range(3)
    )
    return Analysis(facts=facts, claims=claims)


def ready_card():
    return Battlecard(
        competitor="竞品",
        own_product="我方",
        as_of=date(2026, 11, 3),
        status="ready",
        analysis=analysis(),
        opponent_points=tuple(
            OpponentSellingPoint(
                point=EvidenceReference(evidence_id=f"竞品-{n}"),
                customer_expression=f"可能会关注这个使用条件{n}",
                response=AnalysisReference(claim_id=f"claim-{n}"),
            )
            for n in range(3)
        ),
        our_advantages=tuple(AnalysisReference(claim_id=f"claim-{n}") for n in range(3)),
        discovery_questions=(
            DiscoveryQuestion(
                question="您如何评估这项能力是否适合当前工作流程？",
                rationale=AnalysisReference(claim_id="claim-0"),
            ),
        ),
        do_not_say=(
            DoNotSay(
                prohibited="对方绝对没有这个能力",
                basis="evidence_gap",
                reason="未找到不能证明没有",
                safer_alternative="请按具体版本核验",
            ),
        ),
    )


def modify(card, **changes):
    payload = card.model_dump()
    payload.update(changes)
    return Battlecard.model_validate(payload)


def test_four_reader_contracts_and_core_battlecard_priority():
    assert {spec.audience for spec in READER_VIEWS} == set(Audience)
    sales = next(spec for spec in READER_VIEWS if spec.audience == Audience.SALES_REP)
    assert sales.layout == "one_page" and sales.priority == "core"
    assert ReaderViewSpec(
        audience="investment_team", title="投资人", sections=("风险",)
    ).audience == ("investment_team")


def test_ready_battlecard_is_bound_to_shared_evidence_and_roundtrips():
    card = ready_card()
    assert Battlecard.model_validate_json(card.model_dump_json()) == card
    assert card.opponent_points[0].expression_kind == "hypothetical"


def test_empty_draft_discloses_gaps_instead_of_fabricating_three_points():
    card = Battlecard(
        competitor="未知品牌", as_of=date(2026, 11, 3), gaps=("缺少我方背景和双方证据",)
    )
    assert card.status == "draft" and card.analysis is None
    assert card.our_advantages == card.opponent_points == ()
    with pytest.raises(ValidationError):
        modify(card, gaps=())
    with pytest.raises(ValidationError):
        modify(card, status="ready")


@pytest.mark.parametrize(
    "changes",
    [
        {"our_advantages": ()},
        {"opponent_points": ()},
        {"discovery_questions": ()},
        {"do_not_say": ()},
        {"gaps": ("资料过时待核验",)},
        {"own_product": None},
    ],
)
def test_ready_card_rejects_incomplete_sections(changes):
    with pytest.raises(ValidationError):
        modify(ready_card(), **changes)


def test_duplicate_points_cannot_fill_quota():
    card = ready_card()
    for changes in (
        {"our_advantages": (card.our_advantages[0],) * 3},
        {"opponent_points": (card.opponent_points[0],) * 3},
    ):
        with pytest.raises(ValidationError):
            modify(card, **changes)


def test_unknown_refs_and_other_product_selling_points_are_rejected():
    card = ready_card()
    with pytest.raises(ValidationError):
        modify(card, our_advantages=(AnalysisReference(claim_id="invented"),) * 3)
    for evidence_id in ("invented", "我方-0"):
        points = list(card.opponent_points)
        raw = points[0].model_dump()
        raw["point"] = EvidenceReference(evidence_id=evidence_id)
        points[0] = OpponentSellingPoint.model_validate(raw)
        with pytest.raises(ValidationError):
            modify(card, opponent_points=tuple(points))


def test_our_advantages_require_both_sides_evidence_not_just_competitor_claims():
    card = ready_card()
    shared = card.analysis.model_dump()
    shared["claims"][0]["evidence_ids"] = ("竞品-0",)
    with pytest.raises(ValidationError):
        modify(card, analysis=Analysis.model_validate(shared))
    with pytest.raises(ValidationError):
        modify(card, own_product=None, status="draft", gaps=("未提供我方背景",))


def test_customer_language_cannot_be_reported_as_observed_without_records():
    point = ready_card().opponent_points[0]
    raw = point.model_dump()
    raw["expression_kind"] = "observed"
    with pytest.raises(ValidationError):
        OpponentSellingPoint.model_validate(raw)
    raw["observation_refs"] = (EvidenceReference(evidence_id="竞品-0"),)
    assert OpponentSellingPoint.model_validate(raw).expression_kind == "observed"


def test_contradictions_need_evidence_and_ambiguous_analysis_ids_are_rejected():
    with pytest.raises(ValidationError):
        DoNotSay(prohibited="x", basis="contradicted", reason="x", safer_alternative="x")
    card = ready_card()
    shared = card.analysis.model_dump()
    shared["claims"] = shared["claims"] + (shared["claims"][0],)
    with pytest.raises(ValidationError):
        modify(card, analysis=Analysis.model_validate(shared))
