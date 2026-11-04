"""Readiness feedback must not change request, state or persistence semantics."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from steal_her_heart.command_router import CommandResponse, route_request
from steal_her_heart.execution_readiness import (
    pending_guidance,
    profile_saved_guidance,
    render_guidance,
)
from steal_her_heart.memory import MemoryStore
from steal_her_heart.orchestrator import Orchestrator
from steal_her_heart.schemas import ExecutionGuidance, ResearchState, TaskRequest, TaskResult
from steal_her_heart.tools import ToolRegistry


@pytest.mark.parametrize("project_selected", [False, True])
@pytest.mark.parametrize(
    "input_text,required,has_prompt",
    [
        ("/analyze 任意品牌", {"semantic_extraction", "evidence_validation"}, True),
        ("/battlecard 任意品牌", {"battlecard_view"}, True),
        ("/brief 周报", {"research_asset_query", "periodic_reader_views"}, False),
    ],
)
def test_pending_explains_real_gaps_without_starting_research(
    initialized_project, project_selected, input_text, required, has_prompt
):
    response = route_request(
        TaskRequest(user_input=input_text), initialized_project if project_selected else None
    )
    assert response.execution == "pending_module" and response.output_path is None
    guide = response.guidance
    assert guide.research_ready is False and guide.reason == "research_chain_unavailable"
    assert required.issubset({item.id for item in guide.missing_modules})
    assert {"web_search", "web_fetch"}.isdisjoint(item.id for item in guide.missing_modules)
    assert guide.next_steps and bool(guide.codex_prompt) == has_prompt
    assert response.message == guide.summary
    assert response.initialization_question is None


@pytest.mark.parametrize("input_text", ["/analyze", "/brief", "/unknown"])
def test_missing_arguments_and_unsupported_requests_keep_their_original_question(input_text):
    result = route_request(TaskRequest(user_input=input_text))
    assert result.execution in {"needs_clarification", "unsupported"}
    assert result.guidance is None and result.intent.question
    assert result.message == result.intent.question


def test_explicit_unfinished_project_keeps_its_own_next_question():
    result = route_request(TaskRequest(user_input="/analyze 品牌"), initialization_question="问题")
    assert result.execution == "initialization_required"
    assert result.initialization_question == "问题" and result.guidance is None


def test_guidance_is_deterministic_and_action_specific_lists_do_not_mutate_each_other():
    analyze = pending_guidance("analyze", competitor="品牌")
    for _ in range(3):
        card = pending_guidance("battlecard", competitor="品牌")
        brief = pending_guidance("brief", period="monthly")
        assert card != brief
        assert pending_guidance("analyze", competitor="品牌") == analyze
    assert "battlecard_view" not in {item.id for item in analyze.missing_modules}
    assert "research_asset_query" not in {item.id for item in analyze.missing_modules}


def test_profile_ready_is_configuration_only_with_a_separate_planning_bridge_gap():
    guide = profile_saved_guidance()
    assert guide.reason == "profile_saved_only" and guide.research_ready is False
    assert {"project_planning_bridge", "confirmation_lock_bridge"}.issubset(
        item.id for item in guide.missing_modules
    )
    rendered = render_guidance(guide)
    assert guide.summary in rendered
    assert all(item.description in rendered for item in guide.missing_modules)
    assert all(step in rendered for step in guide.next_steps)


def test_human_guidance_escapes_terminal_controls_without_changing_the_request():
    target = "品牌\x1b[2J\x9b31m"
    response = route_request(TaskRequest(user_input="/analyze " + target))
    assert response.intent.competitor == target
    assert target in response.guidance.codex_prompt
    human = render_guidance(response.guidance)
    assert "\x1b" not in human and "\x9b" not in human
    assert "\\x1b[2J\\x9b31m" in human
    assert "\n" in human


def test_old_response_payloads_remain_readable_by_the_new_models():
    old_task = {
        "task_id": str(uuid4()),
        "state": "UNDERSTANDING",
        "outcome": "pending_module",
        "message": "Legacy pending output",
        "report_path": None,
    }
    assert TaskResult.model_validate(old_task).guidance is None
    old_command = route_request(TaskRequest(user_input="/analyze 品牌")).model_dump()
    del old_command["guidance"]
    assert CommandResponse.model_validate(old_command).guidance is None


def test_guidance_cannot_override_clarification_completion_or_report_guards():
    clarify = route_request(TaskRequest(user_input="/brief")).model_dump()
    clarify["guidance"] = pending_guidance("brief").model_dump()
    with pytest.raises(ValidationError):
        CommandResponse.model_validate(clarify)
    with pytest.raises(ValidationError):
        TaskResult(
            task_id=uuid4(),
            state=ResearchState.COMPLETED,
            outcome="completed",
            message="done",
            guidance=pending_guidance(),
        )
    with pytest.raises(ValidationError):
        TaskResult(
            task_id=uuid4(),
            state=ResearchState.UNDERSTANDING,
            outcome="pending_module",
            message="pending",
            report_path="fake-report.md",
            guidance=pending_guidance(),
        )
    guide = pending_guidance().model_dump()
    with pytest.raises(ValidationError):
        ExecutionGuidance.model_validate({**guide, "research_ready": True})
    with pytest.raises(ValidationError):
        ExecutionGuidance.model_validate({**guide, "missing_modules": []})


def test_new_task_guidance_does_not_change_memory_or_repeat_execution(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = MemoryStore(path)
    first = Orchestrator(store, ToolRegistry()).run_task("分析某品牌")
    before = store.load(first.task_id)
    events = store.events(first.task_id)
    assert first.guidance.research_ready is False
    reopened = Orchestrator(MemoryStore(path), ToolRegistry())
    assert reopened.resume(first.task_id) == first
    assert reopened.status(first.task_id) == first
    assert reopened.memory.load(first.task_id) == before
    assert reopened.memory.events(first.task_id) == events
    assert before.memory.sources == before.memory.evidence == ()
    assert before.context.search_count == before.context.llm_count == 0
    assert "guidance" not in before.model_dump_json()
