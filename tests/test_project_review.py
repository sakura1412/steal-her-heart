"""Real local review APIs with synthetic user callbacks and isolated documents."""

import json
import os
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from steal_her_heart import project_review as review
from steal_her_heart.project_workspace import DRAFT_DIMENSIONS, ProjectWorkspace
from steal_her_heart.research_workflow import (
    CompetitorApproval,
    DimensionLibrary,
    DimensionLock,
    DimensionProposal,
    DiscoveryCandidate,
    DiscoveryProposal,
    TierGap,
    digest,
    prepare_collection_jobs,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("No network"))


@pytest.fixture
def setup_review(tmp_path, project_store):
    workspace = ProjectWorkspace(tmp_path / "projects", "synthetic-project")
    workspace.initialize_documents(project_store.load())
    project = workspace.read_required().context
    discovery = DiscoveryProposal(
        project_id=project.project_id,
        project_context_digest=digest(project),
        search_call_ids=("synthetic-source-not-a-real-search",),
        candidates=(
            DiscoveryCandidate(
                id="synthetic-a",
                name="合成硬件甲",
                tier="direct",
                rationale="Synthetic user-provided scope, not verified research",
                basis="user_provided",
            ),
            DiscoveryCandidate(
                id="synthetic-status-quo",
                name="保持当前人工流程",
                tier="alternative",
                solution_type="status_quo",
                rationale="Synthetic alternative hypothesis",
                basis="hypothesis",
            ),
        ),
        tier_gaps=tuple(
            TierGap(tier=tier, reason="Synthetic fixture gap")
            for tier in ("potential_entrant", "benchmark")
        ),
        alternative_assessment="Synthetic fixture considered manual and do-nothing paths",
    )
    request = review.ProjectReviewRequest(
        discovery=discovery,
        selected_ids=("synthetic-a", "synthetic-status-quo"),
        library=DimensionLibrary(version="synthetic-library-v1"),
        core_field_ids=("positioning", "prices"),
        selection_rationale="Explicit synthetic choice; no automated dimension recommendation",
    )
    return workspace, request


def approve(question):
    return question.token


def assert_no_delivery(result):
    assert result.status in {"rejected", "failed"}
    assert not result.scope_locked and not result.research_ready and not result.jobs


def test_two_real_confirmations_persist_and_reopen_the_exact_locked_scope(setup_review):
    workspace, request = setup_review
    stages = []

    def confirm(question):
        stages.append(question.stage)
        if question.stage == "competitors":
            assert "synthetic-a" in question.markdown and "合成硬件甲" in question.markdown
            assert "alternative" in question.markdown and "benchmark" in question.markdown
            assert not (workspace.path / "dimensions.proposal.json").exists()
        else:
            assert "positioning" in question.markdown and "prices" in question.markdown
            assert "提案 SHA-256" in question.markdown
            assert list((workspace.path / ".state/reviews").glob("*/competitor-approval.json"))
            assert not (workspace.path / "dimensions.lock.json").exists()
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert stages == ["competitors", "dimensions"]
    assert result.status == "locked" and result.scope_locked and not result.research_ready
    folder = Path(result.record_path).parent
    assert review.ProjectReviewResult.model_validate_json(Path(result.record_path).read_text()) == (
        result
    )
    project = ProjectWorkspace(workspace.root, workspace.path.name).read_required().context
    discovery = DiscoveryProposal.model_validate_json((folder / "discovery.json").read_text())
    approval = CompetitorApproval.model_validate_json(
        (folder / "competitor-approval.json").read_text()
    )
    dimensions = DimensionProposal.model_validate_json(
        (folder / "dimension-proposal.json").read_text()
    )
    lock = DimensionLock.model_validate_json((folder / "dimension-lock.json").read_text())
    assert result.jobs == prepare_collection_jobs(
        workspace.path / "dimensions.md", project, discovery, approval, dimensions, lock
    )
    assert [job.candidate.id for job in result.jobs] == list(request.selected_ids)
    assert all(job.template == dimensions.template for job in result.jobs)
    assert not (workspace.path / ".state/memory.sqlite3").exists()
    assert not (workspace.path / "research").exists()


@pytest.mark.parametrize(
    "stage,reply",
    [
        ("competitors", "yes"),
        ("competitors", "approved_by=user"),
        ("competitors", ""),
        ("competitors", EOFError),
        ("competitors", KeyboardInterrupt),
        ("dimensions", "reject"),
        ("dimensions", EOFError),
        ("dimensions", KeyboardInterrupt),
    ],
)
def test_rejection_or_interruption_cannot_approve_a_later_stage(setup_review, stage, reply):
    workspace, request = setup_review
    seen = []

    def confirm(question):
        seen.append(question.stage)
        if question.stage != stage:
            return question.token
        if isinstance(reply, type):
            raise reply()
        return reply

    result = review.run_project_review(workspace, request, confirm)
    assert result.status == "rejected"
    assert_no_delivery(result)
    assert not (workspace.path / "dimensions.lock.json").exists()
    assert seen == (["competitors"] if stage == "competitors" else ["competitors", "dimensions"])
    record = json.loads(Path(result.record_path).read_text())
    assert record["status"] == "rejected"
    decision = json.loads((Path(result.record_path).parent / f"{stage}-decision.json").read_text())
    assert decision["accepted"] is False
    assert "reply" not in decision  # Do not persist arbitrary entered secrets/text.


def test_token_is_bound_to_review_stage_and_cannot_be_reused(setup_review):
    workspace, request = setup_review
    old_tokens = []

    def reject_with_saved_token(question):
        old_tokens.append(question.token)
        return "reject"

    first = review.run_project_review(workspace, request, reject_with_saved_token)
    second = review.run_project_review(workspace, request, lambda question: old_tokens[0])
    assert first.review_id != second.review_id
    assert second.status == "rejected"
    assert not (workspace.path / "dimensions.proposal.json").exists()
    tokens = []

    def replay_stage_token(question):
        tokens.append(question.token)
        return tokens[0]

    third = review.run_project_review(workspace, request, replay_stage_token)
    assert tokens[0] != tokens[1] and third.status == "rejected"
    assert not (workspace.path / "dimensions.lock.json").exists()


@pytest.mark.parametrize(
    "stage,target",
    [
        ("competitors", "profile.md"),
        ("dimensions", "profile.md"),
        ("competitors", "dimensions.md"),
        ("dimensions", "dimensions.md"),
        ("dimensions", "dimensions.proposal.json"),
        ("competitors", "request.json"),
        ("competitors", "discovery.json"),
        ("dimensions", "competitor-approval.json"),
        ("dimensions", "dimension-proposal.json"),
        ("competitors", "competitors-question.json"),
        ("dimensions", "competitors-question.json"),
        ("dimensions", "competitors-decision.json"),
    ],
)
def test_files_changed_while_waiting_cannot_lock(setup_review, stage, target):
    workspace, request = setup_review

    def confirm(question):
        if question.stage == stage:
            path = workspace.path / target
            if not path.exists():
                path = next((workspace.path / ".state/reviews").glob(f"*/{target}"))
            path.write_text(path.read_text() + "\nCHANGED SYNTHETIC CONTENT")
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert result.status == "failed"
    assert_no_delivery(result)
    assert not (workspace.path / "dimensions.lock.json").exists()


def test_profile_line_ending_change_also_invalidates_the_current_question(setup_review):
    workspace, request = setup_review

    def confirm(question):
        profile = workspace.path / "profile.md"
        profile.write_bytes(profile.read_bytes().replace(b"\n", b"\r\n"))
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert result.error_code == "REVIEW_SCOPE_CHANGED"
    assert_no_delivery(result)


def test_discovery_prepared_for_a_previous_profile_never_prompts(setup_review):
    workspace, request = setup_review
    profile = workspace.path / "profile.md"
    profile.write_text(profile.read_text() + "\nNew synthetic scope restriction.\n")
    result = review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
    assert result.error_code == "REVIEW_SCOPE_CHANGED"
    assert_no_delivery(result)
    assert (workspace.path / "dimensions.md").read_text() == DRAFT_DIMENSIONS


@pytest.mark.parametrize(
    "existing", ["dimensions.md", "dimensions.proposal.json", "dimensions.lock.json"]
)
def test_existing_artifacts_are_never_overwritten_or_reapproved(setup_review, existing):
    workspace, request = setup_review
    path = workspace.path / existing
    path.write_text("PRESERVE EXISTING ARTIFACT")
    result = review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
    assert result.error_code == "REVIEW_EXISTING_ARTIFACT"
    assert path.read_text() == "PRESERVE EXISTING ARTIFACT"
    assert_no_delivery(result)


@pytest.mark.parametrize(
    "record_name,lock_exists",
    [
        ("competitor-approval.json", False),
        ("dimension-proposal.json", False),
        ("dimension-lock.json", True),
        ("result.json", True),
    ],
)
def test_persistence_failure_never_returns_a_locked_result(
    setup_review, monkeypatch, record_name, lock_exists
):
    workspace, request = setup_review
    original = review._write

    def fail_selected_record(path, value):
        if path.name == record_name:
            raise OSError("PRIVATE_STORAGE_DETAILS")
        return original(path, value)

    monkeypatch.setattr(review, "_write", fail_selected_record)
    result = review.run_project_review(workspace, request, approve)
    assert result.status == "failed" and result.error_code == "REVIEW_STORAGE_ERROR"
    assert_no_delivery(result)
    assert (workspace.path / "dimensions.lock.json").exists() == lock_exists
    assert "PRIVATE" not in result.model_dump_json()


def test_lock_readback_failure_does_not_treat_the_lock_file_as_success(setup_review, monkeypatch):
    workspace, request = setup_review
    monkeypatch.setattr(
        review,
        "prepare_collection_jobs",
        lambda *a: (_ for _ in ()).throw(ValueError("PRIVATE_READBACK_DETAILS")),
    )
    result = review.run_project_review(workspace, request, approve)
    assert (workspace.path / "dimensions.lock.json").exists()
    assert result.error_code == "REVIEW_LOCK_READBACK_FAILED"
    assert_no_delivery(result)
    assert json.loads(Path(result.record_path).read_text())["scope_locked"] is False


def test_partial_lock_write_is_preserved_as_failure_and_cannot_be_silently_retried(
    setup_review, monkeypatch
):
    workspace, request = setup_review

    def partial_write(*args, **kwargs):
        (workspace.path / "dimensions.lock.json").write_text("{")
        raise OSError("Synthetic partial lock write")

    monkeypatch.setattr(review, "confirm_dimensions", partial_write)
    result = review.run_project_review(workspace, request, approve)
    assert result.error_code == "REVIEW_LOCK_WRITE_FAILED"
    assert_no_delivery(result)
    assert (workspace.path / "dimensions.lock.json").read_text() == "{"
    again = review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
    assert again.error_code == "REVIEW_EXISTING_ARTIFACT"
    assert_no_delivery(again)


def test_the_existing_record_is_preserved_if_exclusive_publication_fails(tmp_path):
    target = tmp_path / "receipt.json"
    target.write_text("PRESERVE EARLIER RECORD")
    value = review.ReviewQuestion(stage="competitors", token="synthetic", markdown="synthetic")
    with pytest.raises(FileExistsError):
        review._write(target, value)
    assert target.read_text() == "PRESERVE EARLIER RECORD"
    assert not list(tmp_path.glob(".pending-*"))


def test_audit_lock_is_read_back_before_returning_success(setup_review, monkeypatch):
    workspace, request = setup_review
    original = review._write

    def corrupt_lock_archive(path, value):
        original(path, value)
        if path.name == "dimension-lock.json":
            data = json.loads(path.read_text())
            data["markdown_digest"] = "0" * 64
            path.write_text(json.dumps(data))

    monkeypatch.setattr(review, "_write", corrupt_lock_archive)
    result = review.run_project_review(workspace, request, approve)
    assert result.error_code == "REVIEW_RECORD_CHANGED"
    assert_no_delivery(result)


def test_final_audit_read_failure_removes_our_success_receipt(setup_review, monkeypatch):
    workspace, request = setup_review
    original = review._load

    def fail_result_read(path, model):
        if model is review.ProjectReviewResult:
            raise OSError("Synthetic audit read failure")
        return original(path, model)

    monkeypatch.setattr(review, "_load", fail_result_read)
    result = review.run_project_review(workspace, request, approve)
    assert_no_delivery(result)
    assert json.loads(Path(result.record_path).read_text())["status"] == "failed"


def test_cleanup_error_after_result_publication_cannot_leave_a_success_receipt(
    setup_review, monkeypatch
):
    workspace, request = setup_review
    original_link, original_unlink = os.link, Path.unlink
    published = False
    injected = False

    def track_publication(source, destination, **kwargs):
        nonlocal published
        value = original_link(source, destination, **kwargs)
        if Path(destination).name == "result.json":
            published = True
        return value

    def fail_first_temporary_cleanup(path, **kwargs):
        nonlocal injected
        if published and not injected and path.name.startswith(".pending-"):
            injected = True
            raise OSError("Synthetic one-time temporary cleanup failure")
        return original_unlink(path, **kwargs)

    monkeypatch.setattr(os, "link", track_publication)
    monkeypatch.setattr(Path, "unlink", fail_first_temporary_cleanup)
    result = review.run_project_review(workspace, request, approve)
    assert injected and result.error_code == "REVIEW_STORAGE_ERROR"
    assert_no_delivery(result)
    assert json.loads(Path(result.record_path).read_text())["status"] == "failed"
    assert not list(Path(result.record_path).parent.glob(".pending-*"))


def test_a_later_confirmation_cannot_rewrite_an_earlier_valid_decision(setup_review):
    workspace, request = setup_review

    def confirm(question):
        if question.stage == "dimensions":
            receipt = next((workspace.path / ".state/reviews").glob("*/competitors-decision.json"))
            data = json.loads(receipt.read_text())
            data["accepted"] = False
            receipt.write_text(json.dumps(data))
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert result.error_code == "REVIEW_RECORD_CHANGED"
    assert_no_delivery(result)
    assert not (workspace.path / "dimensions.lock.json").exists()


def test_same_project_concurrent_review_is_busy_without_a_second_prompt(setup_review):
    workspace, request = setup_review
    nested = []

    def confirm(question):
        if question.stage == "competitors":
            nested.append(
                review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
            )
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert result.status == "locked"
    assert nested[0].error_code == "REVIEW_BUSY"
    assert_no_delivery(nested[0])


@pytest.mark.parametrize(
    "unsafe",
    [
        "profile.md",
        "dimensions.md",
        "dimensions.lock.json",
        ".state",
        ".state/reviews",
        ".state/review-mutex",
    ],
)
def test_review_rejects_symlink_documents_state_and_audit_targets(setup_review, tmp_path, unsafe):
    workspace, request = setup_review
    external = tmp_path / "external"
    if unsafe in {".state", ".state/reviews"}:
        external.mkdir()
    else:
        external.write_text("EXTERNAL SENTINEL")
    path = workspace.path / unsafe
    if path.exists():
        if path.is_dir():
            # Initialized fixture .state is empty when documents alone are created.
            path.rmdir()
        else:
            path.unlink()
    path.parent.mkdir(exist_ok=True)
    path.symlink_to(external, target_is_directory=external.is_dir())
    result = review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
    assert result.error_code == "REVIEW_UNSAFE_PATH"
    assert_no_delivery(result)
    assert (
        list(external.iterdir()) == []
        if external.is_dir()
        else external.read_text() == ("EXTERNAL SENTINEL")
    )


def test_hardlinked_draft_is_rejected_before_confirmation_without_changing_either_path(
    setup_review, tmp_path
):
    workspace, request = setup_review
    path = workspace.path / "dimensions.md"
    external = tmp_path / "separate-owner-dimensions.md"
    external.write_bytes(path.read_bytes())
    original_bytes = external.read_bytes()
    path.unlink()
    os.link(external, path)
    assert path.stat().st_ino == external.stat().st_ino and path.stat().st_nlink == 2
    result = review.run_project_review(workspace, request, lambda q: pytest.fail("No prompt"))
    assert result.error_code == "REVIEW_UNSAFE_PATH"
    assert_no_delivery(result)
    assert path.read_bytes() == external.read_bytes() == original_bytes
    assert not (workspace.path / "dimensions.lock.json").exists()


def test_terminal_control_characters_are_escaped_in_both_questions(setup_review):
    workspace, request = setup_review
    candidate = request.discovery.candidates[0].model_copy(
        update={"name": "Synthetic\x1b[2J\u202ePRODUCT", "rationale": "line1\nFAKE PROMPT"}
    )
    discovery = request.discovery.model_copy(
        update={"candidates": (candidate, request.discovery.candidates[1])}
    )
    request = request.model_copy(update={"discovery": discovery})
    seen = []

    def confirm(question):
        seen.append(question.markdown)
        assert "\x1b" not in question.markdown and "\u202e" not in question.markdown
        return question.token

    result = review.run_project_review(workspace, request, confirm)
    assert result.status == "locked" and len(seen) == 2
    assert "\\u001b" in seen[0] and "\\u202e" in seen[0]


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"\xff",
        b"[]",
        b'{"discovery":1,"discovery":2}',
        b'{"approved_by":"user"}',
        b'{"value":NaN}',
        b" " * (1048576 + 1),
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_parser_rejects_invalid_oversized_unknown_or_duplicate_json(payload):
    with pytest.raises((ValueError, ValidationError)):
        review.parse_project_review_json(payload)


def test_request_parser_accepts_only_explicit_configuration_not_approval_flags(setup_review):
    _, request = setup_review
    assert review.parse_project_review_json(request.model_dump_json().encode()) == request
    data = request.model_dump(mode="json")
    data["approved_by"] = "user"
    with pytest.raises(ValidationError):
        review.parse_project_review_json(json.dumps(data).encode())


@pytest.mark.parametrize("ids", [("missing",), ("synthetic-a", "synthetic-a"), ()])
def test_request_rejects_unknown_duplicate_or_empty_selection(setup_review, ids):
    _, request = setup_review
    with pytest.raises(ValidationError):
        review.ProjectReviewRequest.model_validate({**request.model_dump(), "selected_ids": ids})


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "locked", "scope_locked": False},
        {"status": "locked", "scope_locked": True},
        {"status": "rejected", "scope_locked": True},
        {"status": "failed", "scope_locked": False},
        {"status": "rejected", "scope_locked": False, "error_code": "invalid"},
    ],
)
def test_result_schema_cannot_claim_an_inconsistent_success(setup_review, changes):
    from uuid import uuid4

    with pytest.raises(ValidationError):
        review.ProjectReviewResult(review_id=uuid4(), record_path="synthetic.json", **changes)
