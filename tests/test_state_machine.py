import pytest

from steal_her_heart.schemas import (
    PendingConfirmation,
    StateContext,
    ValidationResult,
)
from steal_her_heart.schemas import (
    ResearchState as S,
)
from steal_her_heart.state_machine import ALLOWED, InvalidTransition, transition


@pytest.mark.parametrize("source", list(S))
@pytest.mark.parametrize("target", list(S))
def test_all_state_pairs(source, target):
    pending = PendingConfirmation(
        reason="initial_plan", question="确认？", resume_state=S.RESEARCHING, scope_hash="0" * 64
    )
    context = StateContext(
        user_input="x",
        current_state=source,
        confirmation=pending if source == S.WAIT_CONFIRM else None,
        error_code="TEST" if source == S.FAILED else None,
    )
    kwargs = {}
    if target == S.FAILED:
        kwargs["error_code"] = "TEST"
    if target == S.WAIT_CONFIRM:
        kwargs["confirmation"] = pending
    if source == S.WAIT_CONFIRM:
        kwargs["accepted_confirmation_id"] = str(pending.id)
        kwargs["accepted_scope_hash"] = pending.scope_hash
    if target == S.ANALYZING:
        kwargs["validation"] = ValidationResult(verdict="PASS", coverage=1)
    allowed = source not in {S.COMPLETED, S.FAILED} and (
        target == S.FAILED or target in ALLOWED[source]
    )
    if target == S.WAIT_CONFIRM and source != S.PLANNING:
        allowed = False  # This specific pending approval returns to RESEARCHING.
    if source == S.WAIT_CONFIRM and target not in {S.FAILED, S.RESEARCHING}:
        allowed = False
    if allowed:
        result = transition(context, target, **kwargs)
        assert result.current_state == target
        assert result.revision == 1
        assert context.revision == 0
    else:
        with pytest.raises(InvalidTransition):
            transition(context, target, **kwargs)


def test_analysis_requires_validator_and_confirmation_is_bound():
    context = StateContext(user_input="x", current_state=S.VALIDATING)
    with pytest.raises(InvalidTransition):
        transition(context, S.ANALYZING)
    with pytest.raises(InvalidTransition):
        transition(
            context,
            S.ANALYZING,
            validation=ValidationResult(verdict="RESEARCH_MORE", coverage=0.5, gaps=("价格",)),
        )
    pending = PendingConfirmation(
        reason="entity_ambiguity",
        question="哪个 Cursor？",
        resume_state=S.PLANNING,
        scope_hash="0" * 64,
    )
    waiting = transition(
        StateContext(user_input="x", current_state=S.UNDERSTANDING),
        S.WAIT_CONFIRM,
        confirmation=pending,
    )
    with pytest.raises(InvalidTransition):
        transition(waiting, S.PLANNING, accepted_confirmation_id="invented")
    with pytest.raises(InvalidTransition):
        transition(
            waiting,
            S.PLANNING,
            accepted_confirmation_id=str(pending.id),
            accepted_scope_hash="1" * 64,
        )
    assert (
        transition(
            waiting,
            S.PLANNING,
            accepted_confirmation_id=str(pending.id),
            accepted_scope_hash=pending.scope_hash,
        ).confirmation
        is None
    )


def test_reflection_returns_through_planner():
    state = StateContext(user_input="x", current_state=S.VALIDATING)
    planned = transition(state, S.PLANNING)
    assert transition(planned, S.RESEARCHING).current_state == S.RESEARCHING


def test_confirmation_reason_cannot_be_used_in_the_wrong_stage():
    pending = PendingConfirmation(
        reason="initial_plan", question="确认？", resume_state=S.PLANNING, scope_hash="0" * 64
    )
    with pytest.raises(InvalidTransition):
        transition(
            StateContext(user_input="x", current_state=S.UNDERSTANDING),
            S.WAIT_CONFIRM,
            confirmation=pending,
        )
