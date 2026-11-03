"""Phase 1 coordinator. Stops honestly at the first unimplemented module."""

from typing import Optional
from uuid import UUID

from .memory import MemoryStore
from .project_initialization import require_project
from .schemas import (
    ConversationTurn,
    ProjectContext,
    ResearchMemory,
    ResearchState,
    StateContext,
    TaskRequest,
    TaskResult,
    TaskSnapshot,
)
from .state_machine import transition
from .tools import ToolRegistry


class Orchestrator:
    def __init__(
        self, memory: MemoryStore, registry: ToolRegistry, project: Optional[ProjectContext] = None
    ) -> None:
        self.memory = memory
        self.registry = registry
        self.project = project

    def run_task(self, user_input: str) -> TaskResult:
        request = TaskRequest(user_input=user_input)
        project = require_project(self.project)
        context = StateContext(user_input=request.user_input)
        memory = ResearchMemory(
            task_id=context.task_id,
            project_context=project,
            conversations=(ConversationTurn(role="user", text=request.user_input),),
        )
        snapshot = TaskSnapshot(context=context, memory=memory)
        self.memory.create(snapshot)
        return self.resume(context.task_id)

    def resume(self, task_id: UUID) -> TaskResult:
        project = require_project(self.project)
        snapshot = self.memory.load(task_id)
        if snapshot.memory.project_context != project:
            raise ValueError("Task project context is missing or differs; start a new task")
        if snapshot.context.current_state == ResearchState.CREATED:
            snapshot = TaskSnapshot(
                context=transition(snapshot.context, ResearchState.UNDERSTANDING),
                memory=snapshot.memory,
            )
            self.memory.commit(snapshot, expected_revision=snapshot.context.revision - 1)
        return self._result(snapshot)

    def status(self, task_id: UUID) -> TaskResult:
        return self._result(self.memory.load(task_id))

    @staticmethod
    def _result(snapshot: TaskSnapshot) -> TaskResult:
        context = snapshot.context
        if context.current_state == ResearchState.FAILED:
            return TaskResult(
                task_id=context.task_id,
                state=context.current_state,
                outcome="failed",
                message=context.error_code,
            )
        if context.current_state == ResearchState.COMPLETED:
            return TaskResult(
                task_id=context.task_id,
                state=context.current_state,
                outcome="completed",
                message="Stored research task completed",
            )
        if context.current_state == ResearchState.WAIT_CONFIRM:
            return TaskResult(
                task_id=context.task_id,
                state=context.current_state,
                outcome="waiting_confirmation",
                message=context.confirmation.question,
            )
        return TaskResult(
            task_id=context.task_id,
            state=context.current_state,
            outcome="pending_module",
            message="Phase 1 foundation only: Planner and research execution are not implemented",
        )
