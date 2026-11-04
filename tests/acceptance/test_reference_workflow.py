"""Integration acceptance of an explicitly simulated workflow, not src.Orchestrator."""

import asyncio
import json
from pathlib import Path

import pytest

from steal_her_heart.cli import main
from steal_her_heart.memory import MemoryStore
from steal_her_heart.orchestrator import Orchestrator
from steal_her_heart.schemas import ResearchState
from steal_her_heart.source_snapshots import SnapshotStore
from steal_her_heart.tools import ToolRegistry

from .workflow_harness import SIMULATION_HEADER, ReferenceWorkflow, load_case


def run(root, monkeypatch, case="complete", **kwargs):
    workflow = ReferenceWorkflow(root, load_case(case), monkeypatch, **kwargs)
    return workflow, asyncio.run(workflow.run())


def test_reference_brief_to_source_to_oracle_to_temporary_report(tmp_path, monkeypatch):
    workflow, result = run(tmp_path, monkeypatch)
    assert result.context.current_state == ResearchState.COMPLETED
    assert result.report_path.is_relative_to(tmp_path)
    report = result.report_path.read_text()
    assert report.startswith(SIMULATION_HEADER)
    assert "USD 12" in report and "USD 18" in report
    assert "USD 999" not in report and "UNTRUSTED SCRIPT TEXT" not in report
    assert workflow.network.calls == {"search": 2, "fetch": 2}
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 1
    snapshot = workflow.store.load(result.context.task_id)
    assert snapshot.memory.plan.brief == snapshot.memory.brief
    assert len(snapshot.memory.sources) == len(snapshot.memory.evidence) == 2
    assert snapshot.memory.analysis and snapshot.memory.report_refs == (str(result.report_path),)
    evidence_ids = {item.id for item in snapshot.memory.evidence}
    for collection in result.collections:
        assert all(
            set(item.evidence_ids) <= evidence_ids
            for cell in collection.fields
            for item in cell.items
        )
    assert [event.state.value for event in workflow.store.events(result.context.task_id)] == [
        "CREATED",
        "UNDERSTANDING",
        "PLANNING",
        "RESEARCHING",
        "VALIDATING",
        "ANALYZING",
        "REPORTING",
        "COMPLETED",
    ]
    for evidence in snapshot.memory.evidence:
        stored = workflow.snapshots.load(evidence.source.snapshot_ref)
        locator = json.loads(evidence.locator)
        assert stored.text[locator["start"] : locator["end"]] == evidence.quote
        assert evidence.source.url.unicode_string() in report
        assert evidence.source.fetched_at.isoformat() in report
    assert not (tmp_path / "research").exists() and not (tmp_path / "projects").exists()


def test_explicit_no_results_preserves_not_found_and_blocks_analysis_report(tmp_path, monkeypatch):
    workflow, result = run(tmp_path, monkeypatch, "not_found")
    cells = {collection.competitor: collection.fields[0] for collection in result.collections}
    missing = cells["合成工具乙"]
    assert missing.status == "not_found" and missing.display_value == "未找到"
    assert missing.search_attempts[0].outcome == "no_results"
    assert not missing.items and result.validation.coverage == 0.5
    assert result.context.current_state == ResearchState.PLANNING
    assert result.report_path is None and workflow.analyst.calls == workflow.reporter.calls == 0
    assert not list(tmp_path.rglob("SIMULATION-report.md"))


def test_fixture_applicability_requires_real_allowed_field_and_retains_source(
    tmp_path, monkeypatch
):
    workflow, result = run(tmp_path, monkeypatch, "not_applicable")
    cells = {cell.field_id: cell for cell in result.collections[0].fields}
    assert cells["core_offering"].status == "found"
    assert cells["reference_customers"].status == "not_applicable"
    assert cells["reference_customers"].note and not cells["reference_customers"].items
    assert "不适用" in result.report_path.read_text()
    assert len(workflow.sources) == 2
    assert all(workflow.snapshots.load(c.source.snapshot_ref) for c in workflow.candidates)


def test_declared_oracle_conflict_blocks_despite_complete_source_coverage(tmp_path, monkeypatch):
    workflow, result = run(tmp_path, monkeypatch, "conflict")
    assert result.validation.coverage == 1 and result.validation.conflicts
    assert result.validation.verdict == "RESEARCH_MORE"
    assert len(workflow.sources) == 2 and result.collections[0].fields[0].status == "found"
    assert result.context.current_state == ResearchState.PLANNING and result.report_path is None
    assert workflow.analyst.calls == workflow.reporter.calls == 0
    assert "ANALYZING" not in [
        event.state.value for event in workflow.store.events(result.context.task_id)
    ]


def test_fixture_resolution_keeps_both_versions_and_discloses_scripted_resolution(
    tmp_path, monkeypatch
):
    workflow, result = run(tmp_path, monkeypatch, "resolved_by_fixture")
    report = result.report_path.read_text()
    assert "2025-01-01" in report and "2026-11-01" in report
    assert "no automatic conflict resolution" in report
    assert len(workflow.sources) == 2
    for candidate in workflow.candidates:
        stored = workflow.snapshots.load(candidate.source.snapshot_ref)
        assert candidate.quote in stored.text and str(candidate.source.url) in report


@pytest.mark.parametrize(
    "fault,error",
    [
        ("search_restricted", "SEARCH_ACCESS_RESTRICTED"),
        ("unknown_search_layout", "SEARCH_FORMAT_ERROR"),
        ("fetch_restricted", "FETCH_ACCESS_RESTRICTED"),
        ("http_404", "FETCH_HTTP_ERROR"),
        ("tampered_snapshot", "EXTRACT_SNAPSHOT_CORRUPT"),
        ("wrong_text_version", "EXTRACT_SOURCE_MISMATCH"),
    ],
)
def test_prevalidation_failures_cannot_reach_oracle_analysis_or_report(
    tmp_path, monkeypatch, fault, error
):
    workflow, result = run(tmp_path, monkeypatch, fault=fault)
    assert result.context.current_state == ResearchState.FAILED and result.error == error
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 0
    assert result.report_path is None and not list(tmp_path.rglob("SIMULATION-report.md"))
    assert workflow.search_ledger.usage(result.context.task_id) == (1, 30)
    assert workflow.network.calls["search"] == 1
    if fault in {"search_restricted", "unknown_search_layout"}:
        assert workflow.fetch_ledger.usage(result.context.task_id) == (0, 0)
        assert workflow.network.calls["fetch"] == 0
    else:
        assert workflow.fetch_ledger.usage(result.context.task_id) == (1, 60)


def test_permission_failure_has_no_fetch_attempt_and_does_not_trigger_report(tmp_path, monkeypatch):
    workflow, result = run(tmp_path, monkeypatch, capabilities={"brief_recognition", "web_search"})
    assert result.error == "PERMISSION_DENIED" and workflow.network.calls["fetch"] == 0
    assert workflow.fetch_ledger.usage(result.context.task_id) == (0, 0)
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 0


def test_cumulative_budget_stops_the_second_object_before_its_network_call(tmp_path, monkeypatch):
    workflow, result = run(tmp_path, monkeypatch, search_limit=1)
    assert result.error == "SEARCH_BUDGET_EXCEEDED"
    assert workflow.network.calls == {"search": 1, "fetch": 1}
    assert workflow.search_ledger.usage(result.context.task_id) == (1, 1)
    assert len(workflow.candidates) == 1 and workflow.validator.calls == 0
    assert result.report_path is None


def test_recreated_tools_replay_same_requests_without_network_or_source_overwrite(
    tmp_path, monkeypatch
):
    first, first_result = run(tmp_path, monkeypatch)
    first_bytes = first_result.report_path.read_bytes()
    refs = tuple(first.sources)
    second, again = run(
        tmp_path, monkeypatch, attempt="second", task_id=first_result.context.task_id
    )
    assert again.context.current_state == ResearchState.COMPLETED
    assert second.network.calls == {"search": 0, "fetch": 0}
    assert tuple(second.sources) == refs and first_result.report_path.read_bytes() == first_bytes
    assert second.search_ledger.usage(again.context.task_id) == (2, 30)
    assert second.fetch_ledger.usage(again.context.task_id) == (2, 60)
    assert again.report_path != first_result.report_path
    assert again.report_path.read_bytes() == first_bytes
    for candidate in second.candidates:
        assert SnapshotStore(tmp_path / "sources").load(candidate.source.snapshot_ref)


def test_bad_report_reference_and_write_failure_never_mark_completed(tmp_path, monkeypatch):
    workflow, result = run(tmp_path / "bad-reference", monkeypatch, fault="bad_report_reference")
    assert result.error == "FIXTURE_REPORT_SOURCE_MISMATCH" and result.report_path is None
    root = tmp_path / "write-failure"
    destination = root / "first/SIMULATION-report.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("Existing artifact; do not overwrite")
    workflow, result = run(root, monkeypatch)
    assert result.error == "FIXTURE_ARTIFACT_WRITE_FAILED" and result.report_path is None
    assert destination.read_text() == "Existing artifact; do not overwrite"
    assert not workflow.store.load(result.context.task_id).memory.report_refs
    assert result.context.current_state == ResearchState.FAILED


def test_partial_report_write_is_cleaned_without_publishing_or_persisting_refs(
    tmp_path, monkeypatch
):
    workflow = ReferenceWorkflow(tmp_path, load_case("complete"), monkeypatch)
    destination = workflow.attempt_root / "SIMULATION-report.md"
    temporary = workflow.attempt_root / ".SIMULATION-report.md.part"
    original_open = Path.open
    partial_writes = []

    class PartialReportStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, text):
            self.stream.write(text[: max(1, len(text) // 2)])
            self.stream.flush()
            partial_writes.append(temporary.stat().st_size)
            raise OSError("Injected interruption after actual partial report bytes")

    def interrupt_temporary_report(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return PartialReportStream(stream) if path == temporary else stream

    monkeypatch.setattr(Path, "open", interrupt_temporary_report)
    result = asyncio.run(workflow.run())

    assert len(partial_writes) == 1 and partial_writes[0] > 0
    assert result.error == "FIXTURE_ARTIFACT_WRITE_FAILED"
    assert result.context.current_state == ResearchState.FAILED
    assert result.report_path is None and workflow.report_path is None
    assert not destination.exists() and not temporary.exists()
    persisted = workflow.store.load(result.context.task_id)
    assert persisted.context == result.context == workflow.context
    assert persisted.memory == workflow.memory and persisted.memory.report_refs == ()
    states = [event.state for event in workflow.store.events(result.context.task_id)]
    assert states[-1] == ResearchState.FAILED and ResearchState.COMPLETED not in states
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 1


def test_completed_commit_failure_rolls_back_published_report_and_in_memory_state(
    tmp_path, monkeypatch
):
    workflow = ReferenceWorkflow(tmp_path, load_case("complete"), monkeypatch)
    destination = workflow.attempt_root / "SIMULATION-report.md"
    temporary = workflow.attempt_root / ".SIMULATION-report.md.part"
    original_commit = workflow.store.commit
    completed_attempts = []

    def fail_only_completed(snapshot, *, expected_revision):
        if snapshot.context.current_state == ResearchState.COMPLETED:
            completed_attempts.append(
                (
                    workflow.context.current_state,
                    destination.is_file(),
                    temporary.exists(),
                    snapshot.memory.report_refs,
                )
            )
            raise OSError("Injected storage failure before COMPLETED commit")
        return original_commit(snapshot, expected_revision=expected_revision)

    monkeypatch.setattr(workflow.store, "commit", fail_only_completed)
    result = asyncio.run(workflow.run())

    assert completed_attempts == [(ResearchState.REPORTING, True, False, (str(destination),))]
    assert result.error == "FIXTURE_ARTIFACT_WRITE_FAILED"
    assert result.context.current_state == ResearchState.FAILED
    assert result.report_path is None and workflow.report_path is None
    assert not destination.exists() and not temporary.exists()
    persisted = workflow.store.load(result.context.task_id)
    assert persisted.context == result.context == workflow.context
    assert persisted.memory == workflow.memory and persisted.memory.report_refs == ()
    states = [event.state for event in workflow.store.events(result.context.task_id)]
    assert states[-1] == ResearchState.FAILED and ResearchState.COMPLETED not in states
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 1


def test_real_public_entry_still_reports_pending_instead_of_using_test_harness(tmp_path, capsys):
    assert main(["/analyze", "合成工具甲"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["data"]["execution"] == "pending_module" and payload["data"]["output_path"] is None
    )
    assert not (tmp_path / ".steal-her-heart").exists()
    real_agent = Orchestrator(MemoryStore(tmp_path / "actual-foundation.sqlite3"), ToolRegistry())
    result = real_agent.run_task("分析合成工具甲")
    assert result.outcome == "pending_module" and result.state == ResearchState.UNDERSTANDING
    assert result.report_path is None
    assert not real_agent.memory.load(result.task_id).memory.evidence


@pytest.mark.parametrize(
    "input_text,expected_state,error",
    [
        ("分析 Cursor", ResearchState.WAIT_CONFIRM, None),
        ("不要分析任何对象", ResearchState.FAILED, "FIXTURE_BRIEF_NEEDS_INPUT"),
    ],
)
def test_brief_ambiguity_or_unsupported_input_never_reaches_research(
    tmp_path, monkeypatch, input_text, expected_state, error
):
    case = load_case("complete")
    case["request"] = input_text
    workflow = ReferenceWorkflow(tmp_path, case, monkeypatch)
    result = asyncio.run(workflow.run())
    assert result.context.current_state == expected_state and result.error == error
    assert workflow.network.calls == {"search": 0, "fetch": 0}
    assert workflow.validator.calls == workflow.analyst.calls == workflow.reporter.calls == 0
    assert result.report_path is None
