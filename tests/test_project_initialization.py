import json
import sqlite3
from uuid import uuid4

import pytest
from conftest import PROJECT_ANSWERS
from pydantic import ValidationError

from steal_her_heart.cli import main
from steal_her_heart.command_router import route_request
from steal_her_heart.memory import MemoryStore
from steal_her_heart.orchestrator import Orchestrator
from steal_her_heart.project_initialization import (
    QUESTIONS,
    ProjectInitializationRequired,
    ProjectInterview,
    ProjectStore,
    interview_status,
)
from steal_her_heart.schemas import ProjectContext, TaskRequest
from steal_her_heart.tools import ToolRegistry


def complete(**overrides):
    values = {**PROJECT_ANSWERS, **overrides}
    interview = ProjectInterview()
    for field in QUESTIONS:
        interview = interview.answer(field, values[field])
    return interview


def test_interview_starts_with_product_and_does_not_guess_context():
    status = interview_status(ProjectInterview())
    assert status.phase == 0 and status.status == "interviewing"
    assert status.question_id == "product" and status.context is None


def test_empty_competitor_list_is_valid_and_reader_roles_are_open():
    interview = complete()
    context = interview.context
    assert context.known_competitors == ()
    assert "投资人" in context.readers_and_priorities
    assert context.provenance == "user_provided"
    assert context.uncertainties == ("weaknesses",)
    assert interview_status(interview).status == "ready"
    assert ProjectContext.model_validate_json(context.model_dump_json()) == context


@pytest.mark.parametrize("value", ["", "无", "没有", "暂无", "none", "未知"])
def test_competitors_can_be_explicitly_empty(value):
    assert complete(known_competitors=value).context.known_competitors == ()


def test_competitor_names_are_preserved_and_deduplicated():
    assert complete(known_competitors="品牌A、品牌B，品牌A").context.known_competitors == (
        "品牌A",
        "品牌B",
    )


@pytest.mark.parametrize(
    "field",
    [
        "product",
        "target_customers",
        "industry",
        "market_scope",
        "objective",
        "readers_and_priorities",
    ],
)
def test_unknown_core_answers_keep_gate_closed_and_can_be_clarified(field):
    interview = complete(**{field: "待确认"})
    assert interview.context is None and interview.next_field == field
    assert interview.answer(field, PROJECT_ANSWERS[field]).context is not None


def test_wrong_question_empty_required_answer_and_duplicate_response_rejected():
    interview = ProjectInterview()
    with pytest.raises(ValueError):
        interview.answer("market_scope", "国内")
    with pytest.raises(ValidationError):
        interview.answer("product", " ")
    interview = interview.answer("product", "某产品")
    with pytest.raises(ValueError):
        interview.answer("product", "不能覆盖上一题")


def test_interrupted_interview_restores_same_question_and_completed_profile(tmp_path):
    path = tmp_path / "project.sqlite3"
    store = ProjectStore(path)
    assert store.load() is None and not path.exists()
    initial = store.start()
    updated = store.answer("product", "某产品")
    assert ProjectStore(path).load() == updated
    assert store.start() == updated and initial.project_id == updated.project_id
    with pytest.raises(ValueError):
        store.answer("product", "重复提交")
    assert store.load() == updated


@pytest.mark.parametrize(
    "text", ["/analyze 任意品牌", "/battlecard 任意品牌", "/brief 周报", "生成面向四类角色的月报"]
)
def test_commands_require_initialization_before_execution(text, initialized_project):
    request = TaskRequest(user_input=text)
    assert route_request(request).execution == "initialization_required"
    assert route_request(request).initialization_question == QUESTIONS["product"]
    assert route_request(request, initialized_project).execution == "pending_module"


def test_cli_gate_does_not_create_research_or_project_database(tmp_path, capsys):
    db, project = tmp_path / "task.sqlite3", tmp_path / "project.sqlite3"
    prefix = ["--db", str(db), "--project", str(project)]
    assert main(prefix + ["request", "/analyze 任意品牌"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "initialization_required"
    assert main(prefix + ["run", "分析任意品牌"]) == 3
    assert json.loads(capsys.readouterr().out)["phase"] == 0
    assert not db.exists() and not project.exists()


def test_initialization_precedes_task_parameter_questions(initialized_project):
    request = TaskRequest(user_input="/brief")
    assert route_request(request).execution == "initialization_required"
    assert route_request(request, initialized_project).execution == "needs_clarification"


def test_interactive_eof_keeps_answered_fields_for_resume(tmp_path, monkeypatch, capsys):
    path = tmp_path / "project.sqlite3"
    answers = iter(["某产品"])

    def answer(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", answer)
    assert main(["--project", str(path), "init-project", "--interactive"]) == 3
    restored = ProjectStore(path).load()
    assert restored.answers[0].value == "某产品"
    assert restored.next_field == "industry"
    assert "已保存" in capsys.readouterr().err


def test_cli_interview_roundtrip_and_no_repeated_questions(tmp_path, capsys):
    project = tmp_path / "project.sqlite3"
    prefix = ["--project", str(project)]
    assert main(prefix + ["init-project"]) == 3
    assert json.loads(capsys.readouterr().out)["question_id"] == "product"
    for field, value in PROJECT_ANSWERS.items():
        expected = 0 if field == next(reversed(PROJECT_ANSWERS)) else 3
        assert main(prefix + ["answer-project", field, value]) == expected
        capsys.readouterr()
    assert main(prefix + ["init-project"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["question"] is None and status["context"]["known_competitors"] == []


def test_interactive_cli_asks_all_questions_and_keeps_empty_competitors(
    tmp_path, monkeypatch, capsys
):
    values = iter(PROJECT_ANSWERS.values())
    asked = []

    def answer(prompt):
        asked.append(prompt)
        return next(values)

    monkeypatch.setattr("builtins.input", answer)
    assert (
        main(["--project", str(tmp_path / "project.sqlite3"), "init-project", "--interactive"]) == 0
    )
    assert len(asked) == len(QUESTIONS)
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_orchestrator_blocks_bypass_and_keeps_project_snapshot(tmp_path, initialized_project):
    memory = MemoryStore(tmp_path / "task.sqlite3")
    agent = Orchestrator(memory, ToolRegistry())
    with pytest.raises(ProjectInitializationRequired):
        agent.run_task("分析任意品牌")
    agent = Orchestrator(memory, ToolRegistry(), initialized_project)
    result = agent.run_task("分析任意品牌")
    assert memory.load(result.task_id).memory.project_context == initialized_project
    changed = initialized_project.model_dump()
    changed["project_id"] = uuid4()
    with pytest.raises(ValueError):
        Orchestrator(memory, ToolRegistry(), ProjectContext.model_validate(changed)).resume(
            result.task_id
        )


def test_unknown_project_store_schema_is_not_overwritten(tmp_path):
    path = tmp_path / "project.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    store = ProjectStore(path)
    with pytest.raises(ValueError):
        store.load()
    with pytest.raises(ValueError):
        store.start()
