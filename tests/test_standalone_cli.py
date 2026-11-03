"""Default competitor tasks need no business project; explicit bindings remain strict."""

import json
from uuid import UUID, uuid4

import pytest

from steal_her_heart.cli import main
from steal_her_heart.memory import MemoryStore
from steal_her_heart.project_initialization import ProjectInterview
from steal_her_heart.project_workspace import ProjectWorkspace


@pytest.mark.parametrize(
    "command",
    [
        ["analyze", "任意品牌"],
        ["/analyze", "任意品牌"],
        ["request", "/analyze 任意品牌"],
        ["request", "分析任意品牌"],
        ["/battlecard", "任意品牌"],
        ["/brief", "月报"],
    ],
)
def test_default_routes_ignore_a_broken_default_project(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    legacy_default = tmp_path / "projects/default"
    (legacy_default / ".state").mkdir(parents=True)
    files = [
        legacy_default / "profile.md",
        legacy_default / "dimensions.md",
        legacy_default / ".state/project.sqlite3",
    ]
    for file in files:
        file.write_bytes(b"existing unrelated broken project content")
    before = [file.read_bytes() for file in files]
    assert main(command) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["execution"] == "pending_module"
    assert payload["data"]["initialization_question"] is None
    assert [file.read_bytes() for file in files] == before
    assert not (tmp_path / ".steal-her-heart").exists()
    assert not (legacy_default / ".state/memory.sqlite3").exists()


def test_projects_root_alone_does_not_select_a_product_context(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["--projects-root", str(tmp_path / "uncreated"), "/analyze", "任意品牌"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "pending_module"
    assert not (tmp_path / "uncreated").exists()


@pytest.mark.parametrize("custom_db", [False, True])
def test_standalone_run_status_and_resume_need_no_project_and_keep_context_absent(
    tmp_path, monkeypatch, capsys, custom_db
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / ("custom.sqlite3" if custom_db else ".steal-her-heart/memory.sqlite3")
    prefix = ["--db", str(db)] if custom_db else []
    assert main(prefix + ["run", "分析任意品牌"]) == 3
    task = json.loads(capsys.readouterr().out)
    assert task["state"] == "UNDERSTANDING" and task["outcome"] == "pending_module"
    task_id = task["task_id"]
    assert main(prefix + ["status", task_id]) == 0
    assert json.loads(capsys.readouterr().out)["task_id"] == task_id
    assert main(prefix + ["resume", task_id]) == 3
    assert json.loads(capsys.readouterr().out)["outcome"] == "pending_module"
    memory = MemoryStore(db)
    snapshot = memory.load(UUID(task_id))
    assert snapshot.memory.project_context is None
    assert snapshot.context.search_count == snapshot.context.llm_count == 0
    assert len(memory.events(UUID(task_id))) == 2
    assert not (tmp_path / "projects").exists()


@pytest.mark.parametrize("selection", ["legacy", "missing_workspace", "blank_workspace"])
@pytest.mark.parametrize("operation", ["request", "run", "resume"])
def test_explicit_incomplete_project_selection_still_blocks_execution(
    tmp_path, capsys, selection, operation
):
    db = tmp_path / "task.sqlite3"
    prefix = ["--db", str(db)]
    if selection == "legacy":
        prefix += ["--project", str(tmp_path / "missing.sqlite3")]
    else:
        root = tmp_path / "projects"
        prefix += ["--projects-root", str(root), "--project-name", "chosen"]
        if selection == "blank_workspace":
            ProjectWorkspace(root, "chosen").initialize_documents(ProjectInterview())
    argument = str(uuid4()) if operation == "resume" else "/analyze 任意品牌"
    assert main(prefix + [operation, argument]) == 3
    payload = json.loads(capsys.readouterr().out)
    if operation == "request":
        assert payload["data"]["execution"] == "initialization_required"
        assert payload["data"]["initialization_question"]
    else:
        assert payload["phase"] == 0 and payload["status"] == "interviewing"
    assert not db.exists()


@pytest.mark.parametrize("initially_bound", [False, True])
def test_cli_does_not_resume_a_task_under_different_project_binding(
    tmp_path, capsys, project_args, initially_bound
):
    prefix = ["--db", str(tmp_path / "task.sqlite3")]
    initial = project_args if initially_bound else []
    changed = [] if initially_bound else project_args
    assert main(prefix + initial + ["run", "分析任意品牌"]) == 3
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    assert main(prefix + changed + ["resume", task_id]) == 2
    assert "Invalid input" in capsys.readouterr().err


def test_explicit_project_management_retains_default_folder_compatibility(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert main(["/init-project"]) == 3
    assert json.loads(capsys.readouterr().out)["question_id"] == "product"
    assert (tmp_path / "projects/default/profile.md").exists()
    assert (tmp_path / "projects/default/dimensions.md").exists()
