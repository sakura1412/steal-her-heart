"""Local review UI wiring; terminal inputs here are synthetic, not real approvals."""

import io
import json
import os
import socket
import sys
from types import SimpleNamespace

import pytest

from steal_her_heart.cli import main
from steal_her_heart.project_review import ProjectReviewRequest
from steal_her_heart.project_workspace import ProjectWorkspace
from steal_her_heart.research_workflow import (
    DimensionLibrary,
    DiscoveryCandidate,
    DiscoveryProposal,
    TierGap,
    digest,
)


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("No network expected"))


@pytest.fixture
def terminal(monkeypatch, capsys):
    import steal_her_heart.cli as cli

    output = Terminal()
    # pytest replaces its own capture streams between fixture and call phases.
    # Give the UI the two synthetic surfaces without changing capture internals.
    monkeypatch.setattr(cli, "sys", SimpleNamespace(stdin=Terminal(), stderr=output))
    return output


@pytest.fixture
def review_input(tmp_path, project_store):
    workspace = ProjectWorkspace(tmp_path / "projects", "fixture")
    workspace.initialize_documents(project_store.load())
    project = workspace.read_required().context
    discovery = DiscoveryProposal(
        project_id=project.project_id,
        project_context_digest=digest(project),
        search_call_ids=("synthetic-user-provided-proposal",),
        candidates=(
            DiscoveryCandidate(
                id="fixture-candidate",
                name="合成硬件竞品",
                tier="direct",
                rationale="CLI integration fixture; not online research",
                basis="user_provided",
            ),
        ),
        tier_gaps=tuple(
            TierGap(tier=tier, reason="Synthetic fixture has no researched candidate")
            for tier in ("alternative", "potential_entrant", "benchmark")
        ),
        alternative_assessment="Synthetic gap; alternatives still need real discovery",
    )
    request = ProjectReviewRequest(
        discovery=discovery,
        selected_ids=("fixture-candidate",),
        library=DimensionLibrary(version="synthetic-v1"),
        core_field_ids=("positioning", "prices"),
        selection_rationale="Explicit fixture configuration, not automatic recommendation",
    )
    path = tmp_path / "review.json"
    path.write_text(request.model_dump_json())
    args = [
        "--projects-root",
        str(workspace.root),
        "--project-name",
        "fixture",
        "review-project",
        "--review-file",
        str(path),
    ]
    return workspace, path, args


@pytest.mark.parametrize("stdin_tty,stderr_tty", [(False, True), (True, False), (False, False)])
def test_review_requires_both_local_terminal_surfaces_before_file_access(
    tmp_path, monkeypatch, capsys, stdin_tty, stderr_tty
):
    import steal_her_heart.project_review as review

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", Terminal() if stdin_tty else io.StringIO())
    monkeypatch.setattr(sys, "stderr", Terminal() if stderr_tty else io.StringIO())
    monkeypatch.setattr(review, "parse_project_review_json", lambda *a: pytest.fail("No read"))
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("No file access"))
    assert main(["--project-name", "missing", "review-project", "--review-file", "absent"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "REVIEW_INTERACTIVE_REQUIRED"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("global_args", [[], ["--project", "legacy.sqlite3"]])
def test_review_needs_explicit_project_name(tmp_path, terminal, monkeypatch, capsys, global_args):
    monkeypatch.chdir(tmp_path)
    assert main(global_args + ["review-project", "--review-file", "absent"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "REVIEW_PROJECT_REQUIRED"
    assert not list(tmp_path.iterdir())


def test_interactive_cli_connects_both_real_review_stages_without_research(
    review_input, terminal, monkeypatch, capsys
):
    workspace, _, args = review_input
    questions = []

    def approve_displayed_token(prompt):
        questions.append(prompt)
        return terminal.getvalue().rsplit("请逐字输入：", 1)[1].splitlines()[0]

    monkeypatch.setattr("builtins.input", approve_displayed_token)
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "locked" and payload["scope_locked"] is True
    assert payload["research_ready"] is False and len(payload["jobs"]) == 1
    assert len(questions) == 2
    assert "合成硬件竞品" in terminal.getvalue() and "prices" in terminal.getvalue()
    assert (workspace.path / "dimensions.lock.json").is_file()
    assert not (workspace.path / ".state/memory.sqlite3").exists()
    assert not (workspace.path / "research").exists()


@pytest.mark.parametrize("reply", ["yes", "继续", "approved_by=user", ""])
def test_ambiguous_reply_never_locks_or_prompts_for_dimensions(
    review_input, terminal, monkeypatch, capsys, reply
):
    workspace, _, args = review_input
    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt) or reply)
    assert main(args) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected" and not payload["scope_locked"]
    assert payload["jobs"] == [] and len(calls) == 1
    assert not (workspace.path / "dimensions.lock.json").exists()


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_terminal_interruption_is_rejection_not_approval(
    review_input, terminal, monkeypatch, capsys, exception
):
    workspace, _, args = review_input

    def interrupt(prompt):
        raise exception()

    monkeypatch.setattr("builtins.input", interrupt)
    assert main(args) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected" and not payload["scope_locked"]
    assert not (workspace.path / "dimensions.lock.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        b"not json SECRET_FIXTURE",
        b"x" * 1048577,
        b'{"discovery":{},"discovery":{}}',
        b'{"approved_by":"user","reply":"confirmed"}',
    ],
    ids=["malformed", "oversized", "duplicate-keys", "approval-fields"],
)
def test_bad_review_input_is_safe_json_without_project_writes(
    tmp_path, terminal, monkeypatch, capsys, body
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "bad.json"
    path.write_bytes(body)
    assert main(["--project-name", "missing", "review-project", "--review-file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "REVIEW_INPUT_INVALID"
    assert "SECRET_FIXTURE" not in json.dumps(payload)
    assert not (tmp_path / "projects").exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_non_regular_or_symlink_input_is_rejected_without_prompt(
    tmp_path, terminal, monkeypatch, capsys, kind
):
    path = tmp_path / "input.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        target = tmp_path / "target.json"
        target.write_text("{}")
        path.symlink_to(target)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("No confirmation expected"))
    assert main(["--project-name", "missing", "review-project", "--review-file", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "REVIEW_INPUT_INVALID"


def test_help_discloses_preview_and_does_not_offer_automatic_approval(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["review-project", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--review-file" in help_text and "研究执行链路未就绪" in help_text
    assert "--yes" not in help_text
    with pytest.raises(SystemExit) as caught:
        main(["review-project", "--review-file", "absent", "--yes"])
    assert caught.value.code == 2


def test_default_product_entry_does_not_call_project_review(tmp_path, monkeypatch, capsys):
    import steal_her_heart.project_review as review

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review, "run_project_review", lambda *a: pytest.fail("Optional mode only"))
    assert main(["/analyze", "任意行业产品"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "pending_module"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("exception", [ValueError, OSError])
def test_unexpected_local_failure_does_not_emit_success_or_private_exception(
    review_input, terminal, monkeypatch, capsys, exception
):
    import steal_her_heart.project_review as review

    _, _, args = review_input

    def fail(*args):
        raise exception("PRIVATE_FIXTURE_DETAILS")

    monkeypatch.setattr(review, "run_project_review", fail)
    assert main(args) == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "error" and payload["error"]["code"] == "REVIEW_LOCAL_ERROR"
    assert "PRIVATE_FIXTURE_DETAILS" not in output
