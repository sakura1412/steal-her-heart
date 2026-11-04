"""Entry readiness is visible without starting research or changing CLI contracts."""

import io
import json
import socket
from uuid import UUID

import pytest
from conftest import PROJECT_ANSWERS

from steal_her_heart import cli
from steal_her_heart.cli import main
from steal_her_heart.memory import MemoryStore
from steal_her_heart.project_initialization import QUESTIONS, ProjectInterview, ProjectStore
from steal_her_heart.project_workspace import DRAFT_DIMENSIONS, ProjectWorkspace


def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Readiness disclosure must not start network access")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def tree_files(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["hello"],
        ["search"],
        ["fetch"],
        ["extract-candidates"],
        ["init-project"],
        ["/init-project"],
        ["answer-project"],
        ["project-status"],
        ["run"],
        ["status"],
        ["resume"],
        ["recognize-brief"],
        ["brief"],
        ["request"],
        ["analyze"],
        ["/analyze"],
        ["battlecard"],
        ["/battlecard"],
        ["/brief"],
    ],
)
def test_help_discloses_preview_and_paths_before_any_storage_or_network(
    command, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)

    def denied(*args, **kwargs):
        pytest.fail("Help must not initialize a workspace, interview or task database")

    for name in ("ProjectWorkspace", "ProjectStore", "MemoryStore"):
        monkeypatch.setattr(cli, name, denied)
    with pytest.raises(SystemExit) as exited:
        main(command + ["--help"])
    assert exited.value.code == 0
    output = capsys.readouterr()
    assert "开发预览" in output.out and "研究执行链路未就绪" in output.out
    assert "工程验证" in output.out and "诊断" in output.out
    assert "Codex" in output.out and "Python" in output.out and "同步" in output.out
    assert output.err == "" and not list(tmp_path.iterdir())


@pytest.mark.parametrize("with_project", [False, True])
@pytest.mark.parametrize(
    "command,required_module",
    [
        (["/analyze", "任意服务"], "report_generation"),
        (["/battlecard", "任意服务"], "battlecard_view"),
        (["/brief", "周报"], "research_asset_query"),
    ],
)
def test_pending_routes_have_action_specific_guidance_and_single_json_without_research(
    command, required_module, with_project, project_args, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)
    prefix = project_args if with_project else []
    assert main(prefix + command) == 3
    output = capsys.readouterr()
    payload = json.loads(output.out)
    data = payload["data"]
    assert payload["status"] == "ok" and data["execution"] == "pending_module"
    assert data["output_path"] is None
    guidance = data["guidance"]
    assert guidance["research_ready"] is False and guidance["next_steps"]
    assert required_module in {item["id"] for item in guidance["missing_modules"]}
    assert output.err.count(guidance["summary"]) == 1
    assert all(item["description"] in output.err for item in guidance["missing_modules"])
    assert all(step in output.err for step in guidance["next_steps"])
    if guidance["codex_prompt"]:
        assert guidance["codex_prompt"] in output.err
    assert not (tmp_path / ".steal-her-heart").exists()
    assert not list(tmp_path.rglob("report.md")) and not list(tmp_path.rglob("*.lock.json"))


def completed_interview():
    value = ProjectInterview()
    for field, answer in PROJECT_ANSWERS.items():
        value = value.answer(field, answer)
    return value


@pytest.mark.parametrize("command", ["project-status", "init-project", "/init-project"])
def test_existing_profile_ready_does_not_claim_or_start_research(
    command, tmp_path, monkeypatch, capsys
):
    workspace = ProjectWorkspace(tmp_path / "projects", "example")
    workspace.initialize_documents(completed_interview())
    before = tree_files(workspace.path)
    no_network(monkeypatch)
    assert (
        main(["--projects-root", str(tmp_path / "projects"), "--project-name", "example", command])
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "ready"
    assert "档案已保存" in output.err and "只" in output.err and "配置" in output.err
    assert "维度" in output.err and "候选" in output.err and "锁" in output.err
    assert "不会自动" in output.err and "Codex" in output.err
    assert tree_files(workspace.path) == before
    assert not workspace.interview_path.exists()
    assert not list(workspace.path.rglob("*.lock.json"))
    assert not (workspace.path / ".state/memory.sqlite3").exists()


def test_last_answer_ready_still_exits_zero_and_discloses_profile_only(
    tmp_path, monkeypatch, capsys
):
    store = ProjectStore(tmp_path / "interview.sqlite3")
    store.start()
    fields = list(PROJECT_ANSWERS)
    for field in fields[:-1]:
        store.answer(field, PROJECT_ANSWERS[field])
    no_network(monkeypatch)
    assert (
        main(
            [
                "--project",
                str(store.path),
                "answer-project",
                fields[-1],
                PROJECT_ANSWERS[fields[-1]],
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "ready"
    assert "档案已保存" in output.err and "不会自动" in output.err
    assert "没有一条后续 CLI 命令" in output.err
    assert store.load().context is not None
    assert not (tmp_path / ".steal-her-heart").exists()


def test_completed_interactive_interview_sends_prompts_to_stderr_and_only_json_to_stdout(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(PROJECT_ANSWERS.values()) + "\n"))
    assert main(["--project-name", "example", "init-project", "--interactive"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "ready"
    assert QUESTIONS["product"] in output.err and "档案已保存" in output.err
    assert "不会自动" in output.err
    workspace = ProjectWorkspace(tmp_path / "projects", "example")
    assert (workspace.path / "dimensions.md").read_text() == DRAFT_DIMENSIONS
    assert not list(workspace.path.rglob("*.lock.json"))
    assert not (workspace.path / ".state/memory.sqlite3").exists()


@pytest.mark.parametrize(
    "command,execution,code",
    [
        (["analyze"], "needs_clarification", 3),
        (["/battlecard"], "needs_clarification", 3),
        (["/brief"], "needs_clarification", 3),
        (["request", "/unknown x"], "unsupported", 2),
    ],
)
def test_unresolved_inputs_keep_their_question_instead_of_pending_guidance(
    command, execution, code, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)
    assert main(command) == code
    output = capsys.readouterr()
    data = json.loads(output.out)["data"]
    assert data["execution"] == execution and data["intent"]["question"]
    assert data["guidance"] is None and output.err == ""
    assert not list(tmp_path.iterdir())


def test_explicit_incomplete_project_preserves_initialization_question(tmp_path, capsys):
    assert main(["--project", str(tmp_path / "missing.sqlite3"), "analyze", "任意品牌"]) == 3
    output = capsys.readouterr()
    data = json.loads(output.out)["data"]
    assert data["execution"] == "initialization_required"
    assert data["initialization_question"] and data["guidance"] is None
    assert output.err == "" and not (tmp_path / "missing.sqlite3").exists()


def test_run_status_resume_keep_json_exit_and_events_with_consistent_guidance(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)
    db = tmp_path / "tasks.sqlite3"
    prefix = ["--db", str(db)]
    assert main(prefix + ["run", "分析任意品牌"]) == 3
    output = capsys.readouterr()
    first = json.loads(output.out)
    assert first["outcome"] == "pending_module" and first["report_path"] is None
    assert first["guidance"]["research_ready"] is False
    assert output.err.count(first["guidance"]["summary"]) == 1
    for operation, code in [("status", 0), ("resume", 3), ("resume", 3)]:
        assert main(prefix + [operation, first["task_id"]]) == code
        output = capsys.readouterr()
        assert json.loads(output.out) == first
        assert output.err.count(first["guidance"]["summary"]) == 1
    store = MemoryStore(db)
    snapshot = store.load(UUID(first["task_id"]))
    assert snapshot.context.search_count == snapshot.context.llm_count == 0
    assert snapshot.memory.sources == snapshot.memory.evidence == ()
    assert len(store.events(UUID(first["task_id"]))) == 2
    assert not (tmp_path / "projects").exists()


def test_routing_tool_error_and_hello_keep_their_existing_output_contract(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert main(["request", " "]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "INVALID_ARGUMENT"
    assert output.err == ""
    assert main(["hello"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["data"]["message"] == "Hello, Agent!"
    assert output.err == "" and not list(tmp_path.iterdir())
