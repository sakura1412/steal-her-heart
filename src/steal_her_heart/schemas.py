"""Validated module contracts. No product inference is performed here."""

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


Text = Annotated[str, Field(strict=True, min_length=1, max_length=4000)]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
Count = Annotated[int, Field(strict=True, ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, str_strip_whitespace=True
    )


class ResearchState(str, Enum):
    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    WAIT_CONFIRM = "WAIT_CONFIRM"
    RESEARCHING = "RESEARCHING"
    VALIDATING = "VALIDATING"
    ANALYZING = "ANALYZING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskRequest(Contract):
    user_input: Text


UNKNOWN_PROJECT_VALUES = frozenset(
    {
        "未知",
        "不清楚",
        "暂不清楚",
        "暂时不清楚",
        "不知道",
        "不确定",
        "待确认",
        "未确定",
        "不详",
        "unknown",
        "unsure",
        "tbd",
        "n/a",
    }
)


class ReaderProfile(Contract):
    id: Identifier
    label: Text
    priorities: tuple[Text, ...] = Field(min_length=1)
    output_style: Text = "report"


def industry_labels(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(part.strip() for part in re.split(r"[、，,;；/／]", value) if part.strip())
    )


class ProjectContext(Contract):
    schema_version: Literal[1] = 1
    project_id: UUID
    provenance: Literal["user_provided"] = "user_provided"
    product: Text
    target_customers: Text
    strengths: Text
    weaknesses: Text
    industry: Text
    industries: tuple[Text, ...] = ()
    market_scope: Text
    objective: Text
    readers_and_priorities: Text
    readers: tuple[ReaderProfile, ...] = ()
    price_range: Optional[Text] = None
    special_notes: Optional[Text] = None
    known_competitors: tuple[Text, ...] = ()
    uncertainties: tuple[Text, ...] = ()
    profile_sha256: Optional[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = None

    @model_validator(mode="before")
    @classmethod
    def derive_industries(cls, values):
        if isinstance(values, dict) and isinstance(values.get("industry"), str):
            values = dict(values)
            if not values.get("industries"):
                values["industries"] = industry_labels(values["industry"])
        return values

    @model_validator(mode="after")
    def check_core_context(self) -> "ProjectContext":
        if self.industries != industry_labels(self.industry):
            raise ValueError("Industry labels must agree with the original industry description")
        if len({reader.id for reader in self.readers}) != len(self.readers):
            raise ValueError("Configured reader IDs must be unique")
        for field in (
            "product",
            "target_customers",
            "industry",
            "market_scope",
            "objective",
            "readers_and_priorities",
        ):
            if getattr(self, field).casefold() in UNKNOWN_PROJECT_VALUES:
                raise ValueError(f"Project initialization still needs {field}")
        return self


class ResearchBrief(Contract):
    topic: Text
    industry: Text
    objective: Text
    competitors: tuple[Text, ...] = Field(min_length=1)
    dimensions: tuple[Text, ...] = Field(min_length=1)
    assumptions: tuple[Text, ...] = ()


class ResearchWorkItem(Contract):
    id: Identifier
    competitor: Text
    dimension: Text
    action: Literal["search", "fetch", "extract"]
    query: Text
    depends_on: tuple[Identifier, ...] = ()


class ResearchPlan(Contract):
    version: Annotated[int, Field(strict=True, ge=1)] = 1
    brief: ResearchBrief
    tasks: tuple[ResearchWorkItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_tasks(self) -> "ResearchPlan":
        seen: set[str] = set()
        for task in self.tasks:
            if task.id in seen or not set(task.depends_on).issubset(seen):
                raise ValueError("Tasks must have unique IDs in dependency order")
            if task.competitor not in self.brief.competitors:
                raise ValueError("Task competitor is outside the brief")
            if task.dimension not in self.brief.dimensions:
                raise ValueError("Task dimension is outside the brief")
            seen.add(task.id)
        return self


class Source(Contract):
    id: Identifier
    source_name: Text
    url: HttpUrl
    published_at: Optional[datetime] = None
    fetched_at: datetime
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    snapshot_ref: Text


class Evidence(Contract):
    """A source-grounded factual claim, not an opinion or a calibrated probability."""

    id: Identifier
    source: Source
    competitor: Text
    dimension: Text
    fact: Text
    quote: Text
    locator: Text
    confidence: Annotated[float, Field(ge=0, le=1)]
    confidence_reason: Text


class EvidenceBundle(Contract):
    evidence: tuple[Evidence, ...] = ()


class AnalysisClaim(Contract):
    id: Identifier
    kind: Literal["comparison", "swot", "difference", "opportunity"]
    statement: Text
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    limitations: tuple[Text, ...] = ()


class Analysis(Contract):
    facts: tuple[Evidence, ...] = Field(min_length=1)
    claims: tuple[AnalysisClaim, ...]

    @model_validator(mode="after")
    def check_references(self) -> "Analysis":
        ids = {e.id for e in self.facts}
        if len(ids) != len(self.facts):
            raise ValueError("Duplicate evidence IDs")
        for claim in self.claims:
            if not set(claim.evidence_ids).issubset(ids):
                raise ValueError("Analysis references unknown evidence")
        return self


class ValidationResult(Contract):
    verdict: Literal["PASS", "RESEARCH_MORE"]
    coverage: Annotated[float, Field(ge=0, le=1)]
    gaps: tuple[Text, ...] = ()
    conflicts: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def check_verdict(self) -> "ValidationResult":
        if self.verdict == "PASS" and (self.gaps or self.conflicts or self.coverage != 1):
            raise ValueError("PASS requires complete coverage and no unresolved gaps/conflicts")
        return self


class PendingConfirmation(Contract):
    id: UUID = Field(default_factory=uuid4)
    reason: Literal["entity_ambiguity", "initial_plan", "scope_change"]
    question: Text
    resume_state: ResearchState
    scope_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class StateContext(Contract):
    schema_version: Literal[1] = 1
    task_id: UUID = Field(default_factory=uuid4)
    user_input: Text
    current_state: ResearchState = ResearchState.CREATED
    revision: Count = 0
    completed_tasks: tuple[Identifier, ...] = ()
    pending_tasks: tuple[Identifier, ...] = ()
    search_count: Count = 0
    llm_count: Count = 0
    research_round: Count = 0
    current_competitor: Optional[Text] = None
    current_dimension: Optional[Text] = None
    memory_reference: Optional[Identifier] = None
    confirmation: Optional[PendingConfirmation] = None
    error_code: Optional[Identifier] = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_state(self) -> "StateContext":
        if (self.current_state == ResearchState.WAIT_CONFIRM) != (self.confirmation is not None):
            raise ValueError("Only WAIT_CONFIRM must carry a pending confirmation")
        if (self.current_state == ResearchState.FAILED) != (self.error_code is not None):
            raise ValueError("Only FAILED must carry an error code")
        if set(self.completed_tasks) & set(self.pending_tasks):
            raise ValueError("A task cannot be both pending and completed")
        return self


class ConversationTurn(Contract):
    role: Literal["user", "assistant"]
    text: Text


class ResearchMemory(Contract):
    schema_version: Literal[1] = 1
    task_id: UUID
    project_context: Optional[ProjectContext] = None
    conversations: tuple[ConversationTurn, ...] = ()
    brief: Optional[ResearchBrief] = None
    plan: Optional[ResearchPlan] = None
    sources: tuple[Source, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    analysis: Optional[Analysis] = None
    report_refs: tuple[Text, ...] = ()
    lifecycle: Literal["active", "archived"] = "active"


class TaskSnapshot(Contract):
    context: StateContext
    memory: ResearchMemory

    @model_validator(mode="after")
    def check_identity(self) -> "TaskSnapshot":
        if self.context.task_id != self.memory.task_id:
            raise ValueError("Context and memory must belong to the same task")
        return self


class StateEvent(Contract):
    task_id: UUID
    revision: Count
    state: ResearchState
    recorded_at: datetime = Field(default_factory=utc_now)


class MissingModule(Contract):
    id: Identifier
    description: Text


class ExecutionGuidance(Contract):
    reason: Literal["research_chain_unavailable", "profile_saved_only"] = (
        "research_chain_unavailable"
    )
    research_ready: Literal[False] = False
    summary: Text
    missing_modules: tuple[MissingModule, ...] = Field(min_length=1)
    next_steps: tuple[Text, ...] = Field(min_length=1)
    codex_prompt: Optional[Text] = None

    @model_validator(mode="after")
    def unique_modules(self) -> "ExecutionGuidance":
        if len({item.id for item in self.missing_modules}) != len(self.missing_modules):
            raise ValueError("Missing module IDs must be unique")
        return self


class TaskResult(Contract):
    task_id: UUID
    state: ResearchState
    outcome: Literal["pending_module", "waiting_confirmation", "completed", "failed"]
    message: Text
    report_path: Optional[Text] = None
    guidance: Optional[ExecutionGuidance] = None

    @model_validator(mode="after")
    def check_outcome(self) -> "TaskResult":
        expected = {
            ResearchState.COMPLETED: "completed",
            ResearchState.FAILED: "failed",
            ResearchState.WAIT_CONFIRM: "waiting_confirmation",
        }.get(self.state, "pending_module")
        if self.outcome != expected:
            raise ValueError("Task outcome must match the persisted state")
        if self.report_path is not None and self.state != ResearchState.COMPLETED:
            raise ValueError("Only completed tasks may expose a final report")
        if self.guidance is not None and self.outcome != "pending_module":
            raise ValueError("Research readiness guidance belongs only to pending tasks")
        return self


class ReportDraft(Contract):
    markdown: Annotated[str, Field(strict=True, min_length=1, max_length=200000)]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class ToolContext(Contract):
    task_id: UUID
    capabilities: frozenset[str] = frozenset()
    timeout_s: Annotated[float, Field(gt=0, le=60)] = 5.0


class ToolError(Contract):
    code: Identifier
    message: Text


class ToolResult(Contract):
    call_id: UUID = Field(default_factory=uuid4)
    tool: Identifier
    status: Literal["ok", "error"]
    data: Optional[dict[str, object]] = None
    error: Optional[ToolError] = None

    @model_validator(mode="after")
    def check_result(self) -> "ToolResult":
        if self.status == "ok" and (self.data is None or self.error is not None):
            raise ValueError("Successful tools need data and no error")
        if self.status == "error" and (self.data is not None or self.error is None):
            raise ValueError("Failed tools need error and no data")
        return self
