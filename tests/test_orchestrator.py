import json
from uuid import uuid4

import pytest

from steal_her_heart.cli import main
from steal_her_heart.memory import MemoryStore
from steal_her_heart.orchestrator import Orchestrator
from steal_her_heart.schemas import ResearchState
from steal_her_heart.tools import ToolRegistry


def test_task_stops_before_unimplemented_planner_and_resume_is_idempotent(
    tmp_path, initialized_project
):
    path = tmp_path / "memory.sqlite3"
    agent = Orchestrator(MemoryStore(path), ToolRegistry(), initialized_project)
    result = agent.run_task("分析 Cursor")
    assert result.state == ResearchState.UNDERSTANDING
    assert result.outcome == "pending_module"
    assert result.report_path is None
    reopened = Orchestrator(MemoryStore(path), ToolRegistry(), initialized_project)
    assert reopened.resume(result.task_id) == result
    assert reopened.resume(result.task_id) == result
    snapshot = reopened.memory.load(result.task_id)
    assert snapshot.context.llm_count == snapshot.context.search_count == 0
    assert snapshot.memory.conversations[0].text == "分析 Cursor"
    assert snapshot.memory.evidence == ()
    assert len(reopened.memory.events(result.task_id)) == 2


def test_cli_hello_run_status_resume_and_invalid_input(tmp_path, capsys, project_args):
    db = str(tmp_path / "memory.sqlite3")
    assert main(["--db", db, *project_args, "hello"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["message"] == "Hello, Agent!"
    assert not (tmp_path / "memory.sqlite3").exists()
    assert main(["--db", db, *project_args, "run", "分析 Cursor"]) == 3
    task = json.loads(capsys.readouterr().out)
    assert main(["--db", db, *project_args, "status", task["task_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "UNDERSTANDING"
    assert main(["--db", db, *project_args, "resume", task["task_id"]]) == 3
    capsys.readouterr()
    assert main(["--db", db, *project_args, "status", str(uuid4())]) == 2
    assert "unknown task" in capsys.readouterr().err
    assert main(["--db", db, *project_args, "run", "   "]) == 2


def test_standalone_task_saves_no_business_context_and_resumes_without_initialization(tmp_path):
    store = MemoryStore(tmp_path / "standalone.sqlite3")
    result = Orchestrator(store, ToolRegistry()).run_task("分析任意品牌")
    assert result.state == ResearchState.UNDERSTANDING
    assert result.outcome == "pending_module" and result.report_path is None
    snapshot = store.load(result.task_id)
    assert snapshot.memory.project_context is None
    assert snapshot.memory.conversations[0].text == "分析任意品牌"
    assert snapshot.context.search_count == snapshot.context.llm_count == 0
    assert Orchestrator(store, ToolRegistry()).resume(result.task_id) == result
    assert store.load(result.task_id) == snapshot
    assert len(store.events(result.task_id)) == 2


@pytest.mark.parametrize("initially_bound", [False, True])
def test_resume_cannot_switch_between_standalone_and_project_context(
    tmp_path, initialized_project, initially_bound
):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first_context = initialized_project if initially_bound else None
    other_context = None if initially_bound else initialized_project
    result = Orchestrator(store, ToolRegistry(), first_context).run_task("分析任意品牌")
    before = store.load(result.task_id)
    with pytest.raises(ValueError, match="binding differs"):
        Orchestrator(store, ToolRegistry(), other_context).resume(result.task_id)
    assert store.load(result.task_id) == before
    assert len(store.events(result.task_id)) == 2
