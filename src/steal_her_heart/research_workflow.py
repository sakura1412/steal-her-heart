"""Review boundaries for research stages 1–3. No network or subagent executor."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .collection_template import (
    BASE_FIELDS,
    CollectionField,
    CollectionTemplate,
    build_collection_template,
)
from .project_initialization import require_project
from .project_workspace import DRAFT_DIMENSIONS
from .schemas import Contract, Identifier, ProjectContext, ReaderProfile, Source, Text

Tier = Literal["direct", "alternative", "potential_entrant", "benchmark"]
TIERS = frozenset({"direct", "alternative", "potential_entrant", "benchmark"})
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def digest(value: Contract) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


class DiscoveryCandidate(Contract):
    id: Identifier
    name: Text
    tier: Tier
    solution_type: Literal["product", "service", "process", "status_quo"] = "product"
    rationale: Text
    basis: Literal["online_source", "user_provided", "hypothesis"]
    sources: tuple[Source, ...] = ()

    @model_validator(mode="after")
    def check_basis(self) -> "DiscoveryCandidate":
        if self.basis == "online_source" and not self.sources:
            raise ValueError("Online discovery claims need captured sources")
        if self.solution_type == "status_quo" and self.tier != "alternative":
            raise ValueError("Doing nothing or not switching is an alternative")
        return self


class TierGap(Contract):
    tier: Tier
    reason: Text


class DiscoveryProposal(Contract):
    project_id: UUID
    project_context_digest: Digest
    version: Annotated[int, Field(strict=True, ge=1)] = 1
    search_call_ids: tuple[Identifier, ...] = Field(min_length=1)
    candidates: tuple[DiscoveryCandidate, ...] = Field(min_length=1)
    tier_gaps: tuple[TierGap, ...] = ()
    alternative_assessment: Text

    @model_validator(mode="after")
    def check_coverage(self) -> "DiscoveryProposal":
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError("Candidate IDs must be unique")
        represented = {c.tier for c in self.candidates}
        gaps = {g.tier for g in self.tier_gaps}
        if len(gaps) != len(self.tier_gaps) or represented & gaps or represented | gaps != TIERS:
            raise ValueError("Every tier needs candidates or an explicit gap, without invention")
        return self


class CompetitorApproval(Contract):
    project_id: UUID
    proposal_digest: Digest
    selected_ids: tuple[Identifier, ...] = Field(min_length=1)
    approved_at: datetime
    approved_by: Literal["user"] = "user"

    @model_validator(mode="after")
    def check_selection(self) -> "CompetitorApproval":
        if len(set(self.selected_ids)) != len(self.selected_ids):
            raise ValueError("Confirmed candidates must be unique")
        return self


def confirm_competitors(
    proposal: DiscoveryProposal,
    selected_ids: tuple[str, ...],
    *,
    expected_digest: str,
    approved_at: datetime,
) -> CompetitorApproval:
    """Trusted user-interface entry only; never expose approval tools to a model."""
    if digest(proposal) != expected_digest:
        raise ValueError("The reviewed candidate list changed")
    if (
        not selected_ids
        or len(set(selected_ids)) != len(selected_ids)
        or not set(selected_ids) <= {candidate.id for candidate in proposal.candidates}
    ):
        raise ValueError("Select unique candidates from the reviewed list")
    return CompetitorApproval(
        project_id=proposal.project_id,
        proposal_digest=expected_digest,
        selected_ids=selected_ids,
        approved_at=approved_at,
    )


class DimensionProposal(Contract):
    project_id: UUID
    competitor_approval_digest: Digest
    library_version: Text
    template: CollectionTemplate
    selection_rationale: Text
    readers: tuple[ReaderProfile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_readers(self) -> "DimensionProposal":
        if len({r.id for r in self.readers}) != len(self.readers):
            raise ValueError("Configured readers must be unique")
        return self


class DimensionLibrary(Contract):
    version: Text
    extensions: tuple[CollectionField, ...] = ()

    @model_validator(mode="after")
    def check_entries(self) -> "DimensionLibrary":
        ids = [field.id for field in BASE_FIELDS + self.extensions]
        if len(set(ids)) != len(ids):
            raise ValueError("Library dimensions cannot collide with each other or the shared core")
        return self


def design_dimensions(
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    library: DimensionLibrary,
    *,
    extension_ids: tuple[str, ...],
    selection_rationale: str,
    readers: tuple[ReaderProfile, ...],
    core_field_ids: Optional[tuple[str, ...]] = None,
) -> DimensionProposal:
    """Select configured dimensions from a versioned library, after competitor confirmation."""
    entries = {field.id: field for field in library.extensions}
    if len(set(extension_ids)) != len(extension_ids) or not set(extension_ids) <= set(entries):
        raise ValueError("Choose unique extension IDs from the specified dimension library")
    proposal = DimensionProposal(
        project_id=project.project_id,
        competitor_approval_digest=digest(approval),
        library_version=library.version,
        selection_rationale=selection_rationale,
        readers=readers,
        template=build_collection_template(
            industry=project.industry,
            template_id="project-specific",
            extra_fields=tuple(entries[field_id] for field_id in extension_ids),
            core_field_ids=core_field_ids,
        ),
    )
    _review_scope(project, discovery, approval, proposal)
    return proposal


class DimensionLock(Contract):
    project_id: UUID
    proposal_digest: Digest
    markdown_digest: Digest
    competitor_approval_digest: Digest
    approved_at: datetime
    approved_by: Literal["user"] = "user"


def _review_scope(
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    dimensions: DimensionProposal,
) -> None:
    require_project(project)
    if discovery.project_context_digest != digest(project):
        raise ValueError("Discovery was prepared for a different project context revision")
    if not (
        project.project_id == discovery.project_id == approval.project_id == dimensions.project_id
    ):
        raise ValueError("Review artifacts belong to different projects")
    if approval.proposal_digest != digest(discovery):
        raise ValueError("Competitor approval is stale")
    if dimensions.competitor_approval_digest != digest(approval):
        raise ValueError("Dimension design must follow the confirmed competitor selection")
    if not set(approval.selected_ids) <= {c.id for c in discovery.candidates}:
        raise ValueError("The confirmed selection contains unknown candidates")
    if project.readers and project.readers != dimensions.readers:
        raise ValueError("Readers differ from configured project context")


def render_dimensions(
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    dimensions: DimensionProposal,
) -> str:
    _review_scope(project, discovery, approval, dimensions)

    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    selected = {c.id: c for c in discovery.candidates}
    lines = [
        "# 项目研究维度（待用户确认）",
        "",
        f"项目：{project.project_id}",
        f"产品：{cell(project.product)}",
        f"行业：{cell(project.industry)}",
        f"市场：{cell(project.market_scope)}",
        f"目的：{cell(project.objective)}",
        f"维度库版本：{cell(dimensions.library_version)}",
        f"选择依据：{cell(dimensions.selection_rationale)}",
        "",
        "## 已确认研究对象",
        "",
    ]
    lines += [
        f"- {cell(selected[cid].name)}（{selected[cid].tier}）" for cid in approval.selected_ids
    ]
    lines += ["", "## 统一字段", "", "| ID | 模块 | 字段 | 信息类型 |", "|---|---|---|---|"]
    lines += [
        f"| {cell(f.id)} | {cell(f.module)} | {cell(f.label)} | {f.nature} |"
        for f in dimensions.template.fields
    ]
    lines += ["", "## 配置读者与关注点", ""]
    lines += [
        f"- {cell(r.label)} [{cell(r.id)}]：{cell('；'.join(r.priorities))}；"
        f"输出：{cell(r.output_style)}"
        for r in dimensions.readers
    ]
    lines += [
        "",
        "确认锁定后所有竞品使用同一模板；资料未找到、不适用和未采集分别记录。",
        f"提案 SHA-256：{digest(dimensions)}",
        "",
    ]
    return "\n".join(lines)


def write_dimensions(
    path: Path,
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    dimensions: DimensionProposal,
) -> str:
    """Write a concrete review artifact. Refuse accidental overwrites of earlier revisions."""
    content = render_dimensions(project, discovery, approval, dimensions)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x"
    if path.exists():
        if (
            path.read_text() != DRAFT_DIMENSIONS
            or path.with_suffix(".proposal.json").exists()
            or path.with_suffix(".lock.json").exists()
        ):
            raise FileExistsError("Existing dimension reviews cannot be overwritten")
        mode = "w"
    with path.open(mode, encoding="utf-8", newline="") as file:
        file.write(content)
    with path.with_suffix(".proposal.json").open("x", encoding="utf-8") as file:
        file.write(dimensions.model_dump_json(indent=2) + "\n")
    return hashlib.sha256(content.encode()).hexdigest()


def confirm_dimensions(
    path: Path,
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    dimensions: DimensionProposal,
    *,
    expected_digest: str,
    approved_at: datetime,
) -> DimensionLock:
    content = render_dimensions(project, discovery, approval, dimensions).encode()
    stored_proposal = DimensionProposal.model_validate_json(
        path.with_suffix(".proposal.json").read_text()
    )
    if (
        expected_digest != digest(dimensions)
        or path.read_bytes() != content
        or digest(stored_proposal) != expected_digest
    ):
        raise ValueError("The reviewed dimension proposal or dimensions.md changed")
    lock = DimensionLock(
        project_id=project.project_id,
        proposal_digest=expected_digest,
        markdown_digest=hashlib.sha256(content).hexdigest(),
        competitor_approval_digest=digest(approval),
        approved_at=approved_at,
    )
    lock_path = path.with_suffix(".lock.json")
    with lock_path.open("x", encoding="utf-8") as file:
        file.write(lock.model_dump_json(indent=2) + "\n")
    return lock


class CollectionJob(Contract):
    project_id: UUID
    candidate: DiscoveryCandidate
    template: CollectionTemplate
    dimension_lock: DimensionLock
    ownership: Literal["one_candidate_one_subagent"] = "one_candidate_one_subagent"


def prepare_collection_jobs(
    path: Path,
    project: ProjectContext,
    discovery: DiscoveryProposal,
    approval: CompetitorApproval,
    dimensions: DimensionProposal,
    lock: DimensionLock,
) -> tuple[CollectionJob, ...]:
    _review_scope(project, discovery, approval, dimensions)
    stored_lock = DimensionLock.model_validate_json(path.with_suffix(".lock.json").read_text())
    stored_proposal = DimensionProposal.model_validate_json(
        path.with_suffix(".proposal.json").read_text()
    )
    if stored_lock != lock or digest(stored_proposal) != digest(dimensions):
        raise ValueError("Collection needs the exact persisted dimension approval")
    expected_markdown = render_dimensions(project, discovery, approval, dimensions).encode()
    if (
        lock.project_id != project.project_id
        or lock.proposal_digest != digest(dimensions)
        or lock.competitor_approval_digest != digest(approval)
        or path.read_bytes() != expected_markdown
        or lock.markdown_digest != hashlib.sha256(expected_markdown).hexdigest()
    ):
        raise ValueError("Collection cannot start with a stale or edited dimension lock")
    candidates = {c.id: c for c in discovery.candidates}
    return tuple(
        CollectionJob(
            project_id=project.project_id,
            candidate=candidates[cid],
            template=dimensions.template,
            dimension_lock=lock,
        )
        for cid in approval.selected_ids
    )
