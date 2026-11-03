import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from steal_her_heart.audience_views import Audience
from steal_her_heart.cli import main
from steal_her_heart.command_router import (
    CommandIntent,
    RequestRoutingTool,
    parse_request,
    route_request,
)
from steal_her_heart.schemas import TaskRequest, ToolContext
from steal_her_heart.tools import ToolRegistry


def parse(text):
    return parse_request(TaskRequest(user_input=text))


@pytest.mark.parametrize(
    "slash,natural,action,target",
    [
        ("/analyze Cursor", "帮我分析 Cursor", "analyze", "Cursor"),
        ("/analyze 海底捞", "更新海底捞的竞品档案", "analyze", "海底捞"),
        ("/analyze 自定义品牌", "采集并更新自定义品牌", "analyze", "自定义品牌"),
        ("/battlecard Cursor", "生成 Cursor 的销售一页纸", "battlecard", "Cursor"),
        (
            "/battlecard Claude Code",
            "给 Claude Code 做一张 Battlecard",
            "battlecard",
            "Claude Code",
        ),
        ("/battlecard 餐饮品牌A", "制作餐饮品牌A的一页纸", "battlecard", "餐饮品牌A"),
    ],
)
def test_commands_and_natural_language_have_equivalent_intents(slash, natural, action, target):
    first, second = parse(slash), parse(natural)
    assert first.status == second.status == "parsed"
    assert first.action == second.action == action
    assert first.competitor == second.competitor == target
    assert first.audiences == second.audiences


@pytest.mark.parametrize(
    "slash,natural,period",
    [
        ("/brief 周报", "生成面向四类角色的周报", "weekly"),
        ("/brief monthly", "帮我生成一份竞品月报", "monthly"),
        ("/brief weekly", "生成 weekly report", "weekly"),
    ],
)
def test_reports_require_period_and_defer_readers_to_project_config(slash, natural, period):
    for text in (slash, natural):
        intent = parse(text)
        assert intent.status == "parsed" and intent.action == "brief"
        assert intent.period == period and intent.audiences == ()
        assert intent.competitor is None


@pytest.mark.parametrize("text", ["/brief", "/brief 年报", "生成简报", "/analyze", "/battlecard"])
def test_missing_parameters_prompt_for_clarification(text, initialized_project):
    result = route_request(TaskRequest(user_input=text), initialized_project)
    assert result.execution == "needs_clarification"
    assert result.intent.question and result.output_path is None


@pytest.mark.parametrize(
    "text",
    [
        "/analyze A、B",
        "/battlecard A; rm file",
        "/analyze A && echo x",
        "/analyze A 并生成月报",
    ],
)
def test_single_target_requests_do_not_silently_execute_compound_input(text):
    assert parse(text).status == "needs_clarification"


@pytest.mark.parametrize("text", ["/unknown X", "你好", "生成周报然后发送邮件", "不要生成月报"])
def test_unknown_or_compound_natural_requests_are_not_guessed(text):
    assert parse(text).status == "unsupported"


def test_quoted_names_tabs_and_unicode_names_are_preserved():
    assert parse('/analyze "Claude Code"').competitor == "Claude Code"
    assert parse("/battlecard\t任意硬件品牌").competitor == "任意硬件品牌"


def test_parsed_request_is_not_reported_as_generated_output(initialized_project):
    result = route_request(TaskRequest(user_input="/battlecard Cursor"), initialized_project)
    assert result.intent.status == "parsed"
    assert result.execution == "pending_module" and result.output_path is None
    with pytest.raises(ValidationError):
        CommandIntent(
            original_input="/brief", status="parsed", action="brief", audiences=tuple(Audience)
        )


@pytest.mark.parametrize(
    "text,target",
    [
        ("/analyze Notion", "Notion"),
        ("帮我分析海底捞", "海底捞"),
        ("/analyze 任意硬件品牌", "任意硬件品牌"),
    ],
)
def test_named_competitor_is_enough_without_own_product_configuration(text, target):
    result = route_request(TaskRequest(user_input=text))
    assert result.intent.action == "analyze" and result.intent.competitor == target
    assert result.execution == "pending_module"
    assert result.initialization_question is None and result.output_path is None
    assert result.configured_readers == () and result.reader_requirements is None


def test_only_explicit_incomplete_project_context_enables_initialization_gate():
    request = TaskRequest(user_input="/analyze Notion")
    assert route_request(request).execution == "pending_module"
    selected = route_request(request, initialization_question="请补齐指定项目的 profile.md。")
    assert selected.execution == "initialization_required"
    assert selected.initialization_question == "请补齐指定项目的 profile.md。"
    assert selected.configured_readers == ()


@pytest.mark.parametrize("text", ["/analyze", "/battlecard", "/brief"])
def test_missing_request_arguments_ask_about_the_request_without_starting_interview(text):
    response = route_request(TaskRequest(user_input=text))
    assert response.execution == "needs_clarification"
    assert response.intent.question and response.initialization_question is None


@pytest.mark.parametrize("text", ["/battlecard Notion", "/brief 周报"])
def test_other_research_intents_do_not_require_a_full_own_product_profile(text):
    response = route_request(TaskRequest(user_input=text))
    assert response.execution == "pending_module"
    assert response.initialization_question is None
    assert response.output_path is None and response.configured_readers == ()
    assert response.reader_requirements is None


def test_raw_name_in_api_remains_distinct_from_explicit_cli_analyze_entry():
    # The Codex skill can interpret a name in chat. The deterministic API still
    # requires an action, so greetings and arbitrary text do not become research.
    response = route_request(TaskRequest(user_input="Notion"))
    assert response.execution == "unsupported" and response.intent.action is None


def test_registry_checks_capability_and_schema():
    registry = ToolRegistry()
    registry.register(RequestRoutingTool())

    async def call(arguments, capabilities):
        return await registry.invoke(
            "route_request", arguments, ToolContext(task_id=uuid4(), capabilities=capabilities)
        )

    assert (
        asyncio.run(call({"user_input": "/brief"}, frozenset())).error.code == "PERMISSION_DENIED"
    )
    result = asyncio.run(
        call({"user_input": "/brief", "execute": True}, frozenset({"request_routing"}))
    )
    assert result.error.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "args,action",
    [
        (["analyze", "Cursor"], "analyze"),
        (["/analyze", "任意品牌"], "analyze"),
        (["/battlecard", "Claude", "Code"], "battlecard"),
        (["/brief", "周报"], "brief"),
        (["request", "帮我生成一份月报"], "brief"),
        (["request", "/battlecard Cursor"], "battlecard"),
    ],
)
def test_cli_routes_without_creating_db_or_reports(args, action, tmp_path, capsys, project_args):
    db = tmp_path / "memory.sqlite3"
    assert main(["--db", str(db), *project_args] + args) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["intent"]["action"] == action
    assert payload["data"]["execution"] == "pending_module"
    assert not db.exists()


def test_cli_invalid_input_and_unsupported_requests(tmp_path, capsys, project_args):
    db = tmp_path / "memory.sqlite3"
    assert main(["--db", str(db), *project_args, "request", " "]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"
    assert main(["--db", str(db), *project_args, "request", "/unknown x"]) == 2
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "unsupported"
    assert not db.exists()


def test_research_brief_alias_remains_separate_from_periodic_brief(tmp_path, capsys, project_args):
    db = tmp_path / "memory.sqlite3"
    assert main(["--db", str(db), *project_args, "recognize-brief", "分析 Notion"]) == 0
    recognized = json.loads(capsys.readouterr().out)
    assert recognized["tool"] == "recognize_brief"
    assert main(["--db", str(db), *project_args, "brief", "分析 Notion"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == recognized["data"]
    assert main(["--db", str(db), *project_args, "/brief"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "needs_clarification"
    assert not db.exists()
