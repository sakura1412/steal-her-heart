from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from steal_her_heart.collection_template import CollectionField
from steal_her_heart.command_router import route_request
from steal_her_heart.research_workflow import (
    DimensionLibrary,
    DiscoveryCandidate,
    DiscoveryProposal,
    TierGap,
    confirm_competitors,
    confirm_dimensions,
    design_dimensions,
    digest,
    prepare_collection_jobs,
    write_dimensions,
)
from steal_her_heart.schemas import ProjectContext, ReaderProfile, TaskRequest

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def discovery(project):
    return DiscoveryProposal(
        project_id=project.project_id,
        project_context_digest=digest(project),
        search_call_ids=("fixture-search-call",),
        candidates=(
            DiscoveryCandidate(
                id="direct",
                name="直接竞品A",
                tier="direct",
                rationale="测试候选，非联网研究结果",
                basis="user_provided",
            ),
            DiscoveryCandidate(
                id="keep",
                name="客户不买也不换",
                tier="alternative",
                solution_type="status_quo",
                rationale="客户可能保留当前方案，待验证",
                basis="hypothesis",
            ),
        ),
        tier_gaps=(
            TierGap(tier="potential_entrant", reason="本夹具未提供可验证候选"),
            TierGap(tier="benchmark", reason="待用户讨论适当标杆"),
        ),
        alternative_assessment="已检查不买不换、人工处理与其他解决路径；本夹具仅保留现状方案",
    )


def planned(project):
    proposed = discovery(project)
    approved = confirm_competitors(
        proposed, ("direct", "keep"), expected_digest=digest(proposed), approved_at=NOW
    )
    library = DimensionLibrary(
        version="fixture-library-v1",
        extensions=(
            CollectionField(id="industry_quality", module="行业专项", label="用户选定的品质指标"),
        ),
    )
    readers = project.readers or (
        ReaderProfile(
            id="investment_committee",
            label="投资委员会",
            priorities=("市场机会", "威胁与风险"),
            output_style="投资备忘录",
        ),
    )
    dimensions = design_dimensions(
        project,
        proposed,
        approved,
        library,
        extension_ids=("industry_quality",),
        selection_rationale="按本项目目标选择，不是通用强制字段",
        readers=readers,
    )
    return proposed, approved, dimensions, library


def test_discovery_tiers_cover_alternatives_and_missing_tiers_without_invention(
    initialized_project,
):
    proposal = discovery(initialized_project)
    assert proposal.candidates[1].solution_type == "status_quo"
    with pytest.raises(ValidationError):
        DiscoveryCandidate(
            id="wrong",
            name="不换",
            tier="direct",
            solution_type="status_quo",
            rationale="x",
            basis="hypothesis",
        )
    with pytest.raises(ValidationError):
        DiscoveryCandidate(id="fake", name="x", tier="direct", rationale="x", basis="online_source")
    values = proposal.model_dump()
    values["tier_gaps"] = ()
    with pytest.raises(ValidationError):
        DiscoveryProposal.model_validate(values)


@pytest.mark.parametrize("selection", [("invented",), ("direct", "direct"), ()])
def test_only_reviewed_unique_selected_candidates_can_be_confirmed(selection, initialized_project):
    proposal = discovery(initialized_project)
    with pytest.raises(ValueError):
        confirm_competitors(proposal, selection, expected_digest=digest(proposal), approved_at=NOW)


def test_changed_candidate_list_invalidates_confirmation(initialized_project):
    proposal = discovery(initialized_project)
    with pytest.raises(ValueError):
        confirm_competitors(proposal, ("direct",), expected_digest="0" * 64, approved_at=NOW)
    proposed, approved, dimensions, library = planned(initialized_project)
    changed = proposed.model_copy(update={"version": 2})
    with pytest.raises(ValueError):
        design_dimensions(
            initialized_project,
            changed,
            approved,
            library,
            extension_ids=(),
            selection_rationale="x",
            readers=dimensions.readers,
        )


def test_dimension_library_rejects_unselected_or_colliding_ids(initialized_project):
    proposed, approved, dimensions, library = planned(initialized_project)
    with pytest.raises(ValueError):
        design_dimensions(
            initialized_project,
            proposed,
            approved,
            library,
            extension_ids=("not-in-library",),
            selection_rationale="x",
            readers=dimensions.readers,
        )
    with pytest.raises(ValidationError):
        DimensionLibrary(
            version="x", extensions=(CollectionField(id="positioning", module="x", label="x"),)
        )


def test_project_selects_core_dimensions_instead_of_forcing_every_generic_field(
    initialized_project,
):
    proposed, approved, dimensions, library = planned(initialized_project)
    selected = design_dimensions(
        initialized_project,
        proposed,
        approved,
        library,
        extension_ids=("industry_quality",),
        core_field_ids=("positioning", "prices"),
        selection_rationale="本次只研究定位与价格，另加行业品质指标",
        readers=dimensions.readers,
    )
    assert [field.id for field in selected.template.fields] == [
        "positioning",
        "prices",
        "industry_quality",
    ]


def test_dimensions_file_and_second_confirmation_gate_collection(tmp_path, initialized_project):
    proposed, approved, dimensions, _ = planned(initialized_project)
    path = tmp_path / "dimensions.md"
    write_dimensions(path, initialized_project, proposed, approved, dimensions)
    assert "投资委员会" in path.read_text()
    assert "客户不买也不换" in path.read_text()
    assert "industry_quality" in path.read_text()
    with pytest.raises(FileExistsError):
        write_dimensions(path, initialized_project, proposed, approved, dimensions)
    with pytest.raises(ValueError):
        confirm_dimensions(
            path,
            initialized_project,
            proposed,
            approved,
            dimensions,
            expected_digest="0" * 64,
            approved_at=NOW,
        )
    lock = confirm_dimensions(
        path,
        initialized_project,
        proposed,
        approved,
        dimensions,
        expected_digest=digest(dimensions),
        approved_at=NOW,
    )
    jobs = prepare_collection_jobs(path, initialized_project, proposed, approved, dimensions, lock)
    assert len(jobs) == 2 and {job.candidate.id for job in jobs} == {"direct", "keep"}
    assert jobs[0].template == jobs[1].template == dimensions.template
    assert {job.ownership for job in jobs} == {"one_candidate_one_subagent"}
    path.write_text(path.read_text() + "\n用户或模型修改了字段\n")
    with pytest.raises(ValueError):
        prepare_collection_jobs(path, initialized_project, proposed, approved, dimensions, lock)


def test_changing_dimension_or_reader_configuration_invalidates_lock(tmp_path, initialized_project):
    proposed, approved, dimensions, _ = planned(initialized_project)
    path = tmp_path / "dimensions.md"
    write_dimensions(path, initialized_project, proposed, approved, dimensions)
    lock = confirm_dimensions(
        path,
        initialized_project,
        proposed,
        approved,
        dimensions,
        expected_digest=digest(dimensions),
        approved_at=NOW,
    )
    changed = dimensions.model_copy(update={"selection_rationale": "另一范围"})
    with pytest.raises(ValueError):
        prepare_collection_jobs(path, initialized_project, proposed, approved, changed, lock)


def test_brief_readers_come_from_configuration_without_four_role_fallback(initialized_project):
    request = TaskRequest(user_input="/brief 周报")
    unstructured_values = initialized_project.model_dump()
    unstructured_values["readers"] = ()
    unstructured = route_request(request, ProjectContext.model_validate(unstructured_values))
    assert unstructured.intent.audiences == ()
    assert unstructured.reader_requirements == initialized_project.readers_and_priorities
    payload = initialized_project.model_dump()
    payload["readers"] = (
        ReaderProfile(
            id="board", label="董事会", priorities=("投资风险",), output_style="决策摘要"
        ),
    )
    configured = route_request(request, ProjectContext.model_validate(payload))
    assert configured.intent.audiences == ("board",)
    assert configured.configured_readers[0].label == "董事会"


def test_project_context_change_and_missing_persisted_lock_block_collection(
    tmp_path, initialized_project
):
    proposed, approved, dimensions, _ = planned(initialized_project)
    path = tmp_path / "dimensions.md"
    write_dimensions(path, initialized_project, proposed, approved, dimensions)
    lock = confirm_dimensions(
        path,
        initialized_project,
        proposed,
        approved,
        dimensions,
        expected_digest=digest(dimensions),
        approved_at=NOW,
    )
    values = initialized_project.model_dump()
    values["market_scope"] = "另一个市场"
    with pytest.raises(ValueError):
        prepare_collection_jobs(
            path, ProjectContext.model_validate(values), proposed, approved, dimensions, lock
        )
    path.with_suffix(".lock.json").unlink()
    with pytest.raises(FileNotFoundError):
        prepare_collection_jobs(path, initialized_project, proposed, approved, dimensions, lock)
