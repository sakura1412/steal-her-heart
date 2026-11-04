"""Offline synthetic acceptance matrix for existing collection/state boundaries.

No network, real product facts or business report is generated. Explicit fixture
oracles supply missingness/applicability/conflict decisions: these tests exercise
production schemas, exact-source checks and state guards, not an implemented
semantic Validator, automated conflict detector or research asset catalog.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from steal_her_heart.collection_template import (
    CollectedInformation,
    CollectionField,
    CompetitorCollection,
    build_collection_template,
    new_competitor_collection,
)
from steal_her_heart.evidence_candidates import (
    TextSpan,
    extract_candidates,
    verify_candidate,
)
from steal_her_heart.schemas import ResearchState, StateContext, ValidationResult
from steal_her_heart.source_snapshots import SnapshotStore
from steal_her_heart.state_machine import InvalidTransition, transition

DAY = date(2026, 11, 4)
WINDOW_START = date(2026, 11, 1)
UTC_TIME = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)


def lookup(outcome="checked", acquired_on=DAY):
    return {
        "query": "Synthetic fixture lookup; no actual search executed",
        "source_url": "https://matrix.example/lookup",
        "acquired_on": acquired_on,
        "outcome": outcome,
    }


def information(*, nature="fact", acquired_on=DAY):
    # Contract metadata is synthetic. The evidence ID is a fixture reference,
    # not proof that a semantic verifier accepted the stated content.
    return CollectedInformation(
        value="合成夹具中的有限观察，不能用作真实产品事实",
        nature=nature,
        source_urls=("https://matrix.example/source",),
        acquired_on=acquired_on,
        confidence=0.2,
        confidence_reason="合成验收输入；分数不是正确率，未作真实语义核验",
        evidence_ids=("fixture-reference-only",),
    ).model_dump()


def baseline_payload():
    template = build_collection_template(
        template_id="acceptance-boundary-matrix",
        core_field_ids=(
            "positioning",
            "prices",
            "trial_experience",
            "positive_reviews",
            "limitations",
            "recent_changes",
        ),
        extra_fields=(
            CollectionField(
                id="own_comparison",
                module="合成比较场景",
                label="与我方比较",
                nature="inference",
                requires_own_product=True,
                allow_not_applicable=True,
            ),
        ),
    )
    payload = new_competitor_collection(
        "合成对象甲",
        as_of=DAY,
        template=template,
        recent_window_start=WINDOW_START,
    ).model_dump()
    put_field(
        payload,
        {
            "field_id": "positioning",
            "status": "found",
            "items": [information()],
        },
    )
    put_field(
        payload,
        {
            "field_id": "trial_experience",
            "status": "not_applicable",
            "note": "夹具oracle：该场景只比较一次性服务，不研究试用；不证明现实适用性。",
        },
    )
    put_field(
        payload,
        {
            "field_id": "positive_reviews",
            "status": "not_found",
            "search_attempts": [lookup("no_results")],
            "note": "夹具oracle：只检查本次给定的合成评价集，未发现记录；不代表总体没有评价。",
        },
    )
    put_field(
        payload,
        {
            "field_id": "recent_changes",
            "status": "found",
            "items": [information()],
        },
    )
    return payload


def put_field(payload, replacement):
    payload["fields"] = [
        replacement if field["field_id"] == replacement["field_id"] else field
        for field in payload["fields"]
    ]


def get_field(collection, field_id):
    return next(field for field in collection.fields if field.field_id == field_id)


@pytest.mark.parametrize(
    "status,outcomes,note,carry_items,accepted,display",
    [
        ("pending", (), None, False, True, "未采集"),
        ("pending", ("unavailable",), "合成页面受限", False, True, "未采集"),
        ("not_found", (), "没有完成查找", False, False, None),
        ("not_found", ("unavailable",), "合成验证码阻断", False, False, None),
        ("not_found", ("checked",), None, False, False, None),
        ("not_found", ("checked",), "仅检查夹具中的定价页", False, True, "未找到"),
        ("not_found", ("no_results",), "仅检索本次合成资料集", False, True, "未找到"),
        (
            "not_found",
            ("checked", "unavailable"),
            "先检查合成文档，另一来源受限；结论只限已查范围",
            False,
            True,
            "未找到",
        ),
        ("not_found", ("checked",), "查找后未找到", True, False, None),
        ("not_applicable", (), None, False, False, None),
        ("not_applicable", (), "夹具oracle明确排除该价格口径", False, True, "不适用"),
        ("found", (), None, False, False, None),
        ("found", (), None, True, True, "合成夹具中的有限观察，不能用作真实产品事实"),
    ],
    ids=[
        "not-collected",
        "blocked-still-pending",
        "missing-lookup",
        "blocked-not-missing",
        "missing-scope",
        "checked-limited",
        "explicit-empty",
        "partial-source-scope",
        "missing-cannot-carry-fact",
        "na-needs-reason",
        "allowed-na",
        "found-needs-items",
        "source-metadata-present",
    ],
)
def test_lookup_status_matrix_in_a_mixed_collection(
    status, outcomes, note, carry_items, accepted, display
):
    payload = baseline_payload()
    put_field(
        payload,
        {
            "field_id": "prices",
            "status": status,
            "search_attempts": [lookup(outcome) for outcome in outcomes],
            "note": note,
            "items": [information()] if carry_items else [],
        },
    )
    if not accepted:
        with pytest.raises(ValidationError):
            CompetitorCollection.model_validate(payload)
        return
    collection = CompetitorCollection.model_validate(payload)
    assert get_field(collection, "prices").display_value == display
    assert get_field(collection, "trial_experience").display_value == "不适用"
    assert get_field(collection, "positive_reviews").display_value == "未找到"
    assert get_field(collection, "limitations").display_value == "未采集"
    assert CompetitorCollection.model_validate_json(collection.model_dump_json()) == collection


@pytest.mark.parametrize("field_id", ["positioning", "positive_reviews", "recent_changes"])
def test_applicability_reason_does_not_override_required_field_policy(field_id):
    payload = baseline_payload()
    put_field(
        payload,
        {
            "field_id": field_id,
            "status": "not_applicable",
            "note": "这是完整的合成理由，但不能绕过字段定义的禁止不适用约束。",
        },
    )
    with pytest.raises(ValidationError):
        CompetitorCollection.model_validate(payload)


@pytest.mark.parametrize("own_product", [None, "夹具中明确提供的我方背景"])
@pytest.mark.parametrize("status", ["pending", "found", "not_found", "not_applicable"])
def test_own_product_requirement_matrix_keeps_other_missingness_independent(own_product, status):
    payload = baseline_payload()
    payload["own_product"] = own_product
    put_field(
        payload,
        {
            "field_id": "own_comparison",
            "status": status,
            "items": [information(nature="inference")] if status == "found" else [],
            "search_attempts": [lookup()] if status == "not_found" else [],
            "note": "夹具oracle给定比较范围及理由；未进行真实赢输分析。",
        },
    )
    if own_product is None and status != "pending":
        with pytest.raises(ValidationError):
            CompetitorCollection.model_validate(payload)
    else:
        collection = CompetitorCollection.model_validate(payload)
        assert get_field(collection, "own_comparison").status == status
        assert get_field(collection, "positive_reviews").status == "not_found"
        assert get_field(collection, "trial_experience").status == "not_applicable"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-field",
        "duplicate-field",
        "extra-field",
        "review-as-fact",
        "inference-as-fact",
        "future-item",
        "future-lookup",
        "missing-window",
        "reversed-window",
        "missing-window-not-found",
    ],
)
def test_complete_mixed_collection_rejects_cross_field_boundary_corruption(mutation):
    payload = baseline_payload()
    if mutation == "missing-field":
        payload["fields"] = payload["fields"][:-1]
    elif mutation == "duplicate-field":
        payload["fields"].append(payload["fields"][0])
    elif mutation == "extra-field":
        payload["fields"].append({"field_id": "not-in-template", "status": "pending"})
    elif mutation in {"review-as-fact", "inference-as-fact"}:
        target = "positive_reviews" if mutation == "review-as-fact" else "limitations"
        put_field(payload, {"field_id": target, "status": "found", "items": [information()]})
    elif mutation == "future-item":
        put_field(
            payload,
            {
                "field_id": "positioning",
                "status": "found",
                "items": [information(acquired_on=DAY + timedelta(days=1))],
            },
        )
    elif mutation == "future-lookup":
        put_field(
            payload,
            {
                "field_id": "positive_reviews",
                "status": "not_found",
                "note": "合成查找范围",
                "search_attempts": [lookup("no_results", acquired_on=DAY + timedelta(days=1))],
            },
        )
    elif mutation == "reversed-window":
        payload["recent_window_start"] = DAY + timedelta(days=1)
    else:
        payload["recent_window_start"] = None
        if mutation == "missing-window-not-found":
            put_field(
                payload,
                {
                    "field_id": "recent_changes",
                    "status": "not_found",
                    "note": "合成查找范围",
                    "search_attempts": [lookup("no_results")],
                },
            )
    with pytest.raises(ValidationError):
        CompetitorCollection.model_validate(payload)


@pytest.mark.parametrize(
    "coverage,gaps,conflicts",
    [
        (0.0, ("所有格子均缺证据",), ()),
        (0.5, ("价格未找到，不能推断免费",), ()),
        (1.0, ("不适用理由尚未核验",), ()),
        (1.0, (), ("夹具oracle：同口径来源主张冲突",)),
        (0.5, ("缺来源",), ("夹具oracle：同口径来源主张冲突",)),
    ],
)
def test_enumerating_every_field_does_not_authorize_analysis(coverage, gaps, conflicts):
    collection = CompetitorCollection.model_validate(baseline_payload())
    assert len(collection.fields) == len(collection.template.fields)
    # Coverage/gap/conflict values come from a named fixture oracle. Production
    # currently does not compute them from the structurally valid collection.
    validation = ValidationResult(
        verdict="RESEARCH_MORE",
        coverage=coverage,
        gaps=gaps,
        conflicts=conflicts,
    )
    state = StateContext(
        user_input="只验证合成边界，不生成研究结论", current_state=ResearchState.VALIDATING
    )
    with pytest.raises(ValidationError):
        ValidationResult(verdict="PASS", coverage=coverage, gaps=gaps, conflicts=conflicts)
    with pytest.raises(InvalidTransition):
        transition(state, ResearchState.ANALYZING, validation=validation)
    planned = transition(state, ResearchState.PLANNING)
    assert planned.current_state == ResearchState.PLANNING
    assert state.current_state == ResearchState.VALIDATING and state.revision == 0


def test_exact_source_binding_does_not_resolve_a_fixture_conflict_or_erase_old_material(tmp_path):
    store = SnapshotStore(tmp_path / "sources")
    bodies = (
        b"Synthetic Basic plan: USD 9 per seat per month.",
        b"Synthetic Basic plan: USD 12 per seat per month.",
        b"Synthetic correction: the first amount is edition A; the second is edition B.",
    )

    def capture(body):
        document = store.save(
            url="https://matrix.example/pricing",
            media_type="text/plain",
            charset="utf-8",
            body=body,
            fetched_at=UTC_TIME,
        )
        snapshot = store.load(document.source.snapshot_ref)
        candidate = extract_candidates(
            snapshot,
            competitor="合成对象甲",
            dimension="价格口径",
            spans=(TextSpan(start=0, end=len(snapshot.text.rstrip("\n"))),),
        )[0]
        verify_candidate(candidate, snapshot)
        assert candidate.support_status == "unvalidated"
        return document, candidate

    first, first_candidate = capture(bodies[0])
    second, second_candidate = capture(bodies[1])
    conflict = ValidationResult(
        verdict="RESEARCH_MORE",
        coverage=1,
        conflicts=(
            "夹具oracle：两个合成报价看似同口径，但缺少版本依据；不能选择其中一个当当前价格。",
        ),
    )
    conflict_before = conflict.model_dump_json()
    state = StateContext(user_input="核实合成报价口径", current_state=ResearchState.VALIDATING)
    with pytest.raises(InvalidTransition):
        transition(state, ResearchState.ANALYZING, validation=conflict)

    # Adding correction material is not a production conflict-resolution step.
    # It creates another immutable Source; a real Validator must still inspect
    # versions and claim support before accepting a revised conclusion.
    correction, correction_candidate = capture(bodies[2])
    refs = [document.source.snapshot_ref for document in (first, second, correction)]
    assert len(set(refs)) == 3
    assert [store.load(ref).raw for ref in refs] == list(bodies)
    assert conflict.model_dump_json() == conflict_before
    assert first_candidate.source == first.source and second_candidate.source == second.source
    assert correction_candidate.source == correction.source
    assert len({first_candidate.id, second_candidate.id, correction_candidate.id}) == 3
    with pytest.raises(InvalidTransition):
        transition(state, ResearchState.ANALYZING, validation=conflict)
