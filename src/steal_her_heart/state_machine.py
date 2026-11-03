"""Allowed edges and guards; prompts have no authority to change state."""

from typing import Optional

from .schemas import PendingConfirmation, ResearchState, StateContext, ValidationResult

S = ResearchState
ALLOWED = {
    S.CREATED: frozenset({S.UNDERSTANDING}),
    S.UNDERSTANDING: frozenset({S.PLANNING, S.WAIT_CONFIRM}),
    S.PLANNING: frozenset({S.RESEARCHING, S.WAIT_CONFIRM}),
    S.WAIT_CONFIRM: frozenset({S.UNDERSTANDING, S.PLANNING, S.RESEARCHING}),
    S.RESEARCHING: frozenset({S.VALIDATING}),
    S.VALIDATING: frozenset({S.ANALYZING, S.PLANNING, S.WAIT_CONFIRM}),
    S.ANALYZING: frozenset({S.REPORTING}),
    S.REPORTING: frozenset({S.COMPLETED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
}
CONFIRM_RETURNS = {
    S.UNDERSTANDING: frozenset({S.UNDERSTANDING, S.PLANNING}),
    S.PLANNING: frozenset({S.PLANNING, S.RESEARCHING}),
    S.VALIDATING: frozenset({S.PLANNING}),
}
CONFIRM_REASONS = {
    S.UNDERSTANDING: frozenset({"entity_ambiguity"}),
    S.PLANNING: frozenset({"initial_plan", "scope_change"}),
    S.VALIDATING: frozenset({"scope_change"}),
}


class InvalidTransition(ValueError):
    pass


def transition(
    context: StateContext,
    target: ResearchState,
    *,
    confirmation: Optional[PendingConfirmation] = None,
    accepted_confirmation_id: Optional[str] = None,
    accepted_scope_hash: Optional[str] = None,
    validation: Optional[ValidationResult] = None,
    error_code: Optional[str] = None,
) -> StateContext:
    """Return a validated new context without mutating or persisting the original."""
    source = context.current_state
    if source in {S.COMPLETED, S.FAILED}:
        raise InvalidTransition("Terminal tasks cannot transition")
    if target != S.FAILED and target not in ALLOWED[source]:
        raise InvalidTransition(f"Forbidden transition: {source.value} -> {target.value}")
    if target == S.FAILED:
        if not error_code:
            raise InvalidTransition("Failure needs an error code")
    elif error_code is not None:
        raise InvalidTransition("Only FAILED accepts an error code")
    if target == S.WAIT_CONFIRM:
        if (
            confirmation is None
            or confirmation.resume_state not in CONFIRM_RETURNS[source]
            or confirmation.reason not in CONFIRM_REASONS[source]
        ):
            raise InvalidTransition("Confirmation needs a valid return state")
    elif confirmation is not None:
        raise InvalidTransition("Only WAIT_CONFIRM accepts confirmation metadata")
    if source == S.WAIT_CONFIRM and target != S.FAILED:
        pending = context.confirmation
        if (
            pending is None
            or target != pending.resume_state
            or accepted_confirmation_id != str(pending.id)
            or accepted_scope_hash != pending.scope_hash
        ):
            raise InvalidTransition("Confirmation ID and bound return state must match")
    if source == S.VALIDATING and target == S.ANALYZING:
        if validation is None or validation.verdict != "PASS":
            raise InvalidTransition("Analysis requires a passing validation")
    payload = context.model_dump()
    payload.update(
        current_state=target,
        revision=context.revision + 1,
        confirmation=confirmation,
        error_code=error_code,
    )
    return StateContext.model_validate(payload)
