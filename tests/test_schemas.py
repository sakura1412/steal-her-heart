from uuid import uuid4

import pytest
from pydantic import ValidationError

from steal_her_heart.schemas import (
    Analysis,
    AnalysisClaim,
    ResearchBrief,
    ResearchPlan,
    ResearchWorkItem,
    StateContext,
    TaskRequest,
    TaskResult,
    ValidationResult,
)


def test_unknown_fields_and_blank_input_rejected():
    with pytest.raises(ValidationError):
        TaskRequest(user_input="分析 Cursor", secret="x")
    with pytest.raises(ValidationError):
        TaskRequest(user_input="")


def test_context_round_trips_and_rejects_invalid_counters():
    context = StateContext(user_input="分析 Cursor")
    assert StateContext.model_validate_json(context.model_dump_json()) == context
    with pytest.raises(ValidationError):
        StateContext(user_input="x", search_count=-1)
    with pytest.raises(ValidationError):
        StateContext(user_input="x", llm_count="1")


def test_plan_rejects_unknown_competitor_and_dependency_cycles():
    brief = ResearchBrief(
        topic="编程工具",
        industry="软件",
        objective="比较",
        competitors=("Cursor",),
        dimensions=("价格",),
    )
    with pytest.raises(ValidationError):
        ResearchPlan(
            brief=brief,
            tasks=(
                ResearchWorkItem(
                    id="one", competitor="other", dimension="价格", action="search", query="价格"
                ),
            ),
        )
    with pytest.raises(ValidationError):
        ResearchPlan(
            brief=brief,
            tasks=(
                ResearchWorkItem(
                    id="one",
                    competitor="Cursor",
                    dimension="价格",
                    action="search",
                    query="价格",
                    depends_on=("one",),
                ),
            ),
        )


def test_analysis_cannot_exist_without_evidence():
    with pytest.raises(ValidationError):
        Analysis(
            facts=(),
            claims=(
                AnalysisClaim(
                    id="claim",
                    kind="opportunity",
                    statement="机会",
                    evidence_ids=("invented",),
                ),
            ),
        )


def test_validator_cannot_pass_with_gaps():
    with pytest.raises(ValidationError):
        ValidationResult(verdict="PASS", coverage=0.5, gaps=("缺价格",))


def test_task_result_cannot_claim_success_before_completion():
    with pytest.raises(ValidationError):
        TaskResult(task_id=uuid4(), state="UNDERSTANDING", outcome="completed", message="done")
    with pytest.raises(ValidationError):
        TaskResult(
            task_id=uuid4(),
            state="UNDERSTANDING",
            outcome="pending_module",
            message="pending",
            report_path="fake.md",
        )
