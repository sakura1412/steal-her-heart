"""CLI wiring is tested with controlled tools; no provider or network is contacted."""

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from pydantic import Field

from steal_her_heart.cli import main
from steal_her_heart.schemas import Contract, Text, ToolError, ToolResult
from steal_her_heart.tools import Tool, ToolRegistry


class SearchInput(Contract):
    query: Text
    max_results: Annotated[int, Field(strict=True, ge=1, le=10)] = 5
    request_id: UUID = Field(default_factory=uuid4)


class SearchOutput(Contract):
    query: Text
    task_id: UUID
    request_id: UUID
    result_count: int
    usage: str = "discovery_only"


@pytest.fixture
def controlled_search(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {"calls": [], "ledgers": []}

    class Ledger:
        def __init__(self, path):
            self.path = Path(path)
            seen["ledgers"].append(self.path)

    class ControlledTool(Tool):
        name = "web_search"
        description = "Controlled search fixture, without network"
        input_model = SearchInput
        output_model = SearchOutput
        required_capabilities = frozenset({"web_search"})

        def __init__(self, *, ledger):
            self.ledger = ledger

        async def run(self, arguments, context):
            seen["calls"].append((arguments, context))
            return SearchOutput(
                query=arguments.query,
                task_id=context.task_id,
                request_id=arguments.request_id,
                result_count=0,
            )

    ledger_module = ModuleType("steal_her_heart.search_ledger")
    ledger_module.SearchLedger = Ledger
    search_module = ModuleType("steal_her_heart.web_search")
    search_module.WebSearchTool = ControlledTool
    monkeypatch.setitem(sys.modules, ledger_module.__name__, ledger_module)
    monkeypatch.setitem(sys.modules, search_module.__name__, search_module)
    return seen


def test_search_cli_invokes_registered_tool_with_capability_and_bounded_timeout(
    controlled_search, tmp_path, capsys
):
    assert main(["search", "任意产品 官方资料"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["tool"] == "web_search" and payload["status"] == "ok"
    assert payload["data"]["query"] == "任意产品 官方资料"
    assert payload["data"]["usage"] == "discovery_only"
    arguments, context = controlled_search["calls"][0]
    assert arguments.max_results == 5
    assert context.capabilities == frozenset({"web_search"}) and context.timeout_s == 15
    assert f"search_task_id={context.task_id}" in output.err
    assert f"search_request_id={arguments.request_id}" in output.err
    assert controlled_search["ledgers"] == [Path(".steal-her-heart/search-calls.sqlite3")]
    assert not (tmp_path / "projects").exists()
    assert not (tmp_path / ".steal-her-heart").exists()


def test_search_cli_forwards_explicit_ids_and_ledger_path(controlled_search, tmp_path, capsys):
    task_id, request_id = uuid4(), uuid4()
    ledger = tmp_path / "calls.sqlite3"
    assert (
        main(
            [
                "search",
                "查询",
                "--max-results",
                "2",
                "--task-id",
                str(task_id),
                "--request-id",
                str(request_id),
                "--search-ledger",
                str(ledger),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)
    arguments, context = controlled_search["calls"][0]
    assert context.task_id == task_id and arguments.request_id == request_id
    assert arguments.max_results == 2 and controlled_search["ledgers"] == [ledger]
    assert payload["data"]["task_id"] == str(task_id)
    assert payload["data"]["request_id"] == str(request_id)
    assert f"search_request_id={request_id}" in output.err


@pytest.mark.parametrize(
    "args",
    [
        ["search", " "],
        ["search", "x", "--max-results", "0"],
        ["search", "x", "--max-results", "11"],
    ],
)
def test_invalid_search_schema_has_no_provider_calls(controlled_search, capsys, args):
    assert main(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert controlled_search["calls"] == []


def test_access_restriction_is_an_error_not_empty_search_success(
    controlled_search, monkeypatch, capsys
):
    seen = []

    async def restricted(self, name, arguments, context):
        assert name == "web_search"
        seen.append((arguments["request_id"], context.task_id))
        return ToolResult(
            tool=name,
            status="error",
            error=ToolError(
                code="SEARCH_ACCESS_RESTRICTED", message="Provider access is restricted"
            ),
        )

    monkeypatch.setattr(ToolRegistry, "invoke", restricted)
    assert main(["search", "query"]) == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["status"] == "error" and payload["data"] is None
    assert payload["error"]["code"] == "SEARCH_ACCESS_RESTRICTED"
    request_id, task_id = seen[0]
    assert isinstance(request_id, UUID)
    assert f"search_task_id={task_id}" in output.err
    assert f"search_request_id={request_id}" in output.err
    assert controlled_search["calls"] == []


@pytest.mark.parametrize("command", [["hello"], ["/analyze", "任意产品"]])
def test_other_cli_commands_do_not_create_or_initialize_search_ledger(
    controlled_search, capsys, command
):
    assert main(command) in (0, 3)
    capsys.readouterr()
    assert controlled_search["ledgers"] == [] and controlled_search["calls"] == []


def test_diagnostics_do_not_load_explicit_broken_business_profile(controlled_search, capsys):
    assert main(["--project-name", "../invalid", "search", "query"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@pytest.mark.parametrize("option", ["--task-id", "--request-id"])
def test_invalid_identity_is_rejected_before_tool_construction(controlled_search, capsys, option):
    with pytest.raises(SystemExit) as error:
        main(["search", "query", option, "not-a-uuid"])
    assert error.value.code == 2
    assert controlled_search["ledgers"] == [] and controlled_search["calls"] == []
    capsys.readouterr()


@pytest.mark.parametrize("restricted", [False, True])
def test_cli_with_real_search_tool_and_ledger_replays_without_network(
    monkeypatch, tmp_path, capsys, restricted
):
    from steal_her_heart import web_search
    from steal_her_heart.schemas import utc_now
    from steal_her_heart.search_html import SearchHit
    from steal_her_heart.search_ledger import SearchLedger

    monkeypatch.chdir(tmp_path)
    calls = []

    class Provider:
        name = "controlled"

        async def search(self, request, *, timeout_s):
            calls.append(request)
            assert 0 < timeout_s <= 15
            if restricted:
                raise web_search.SearchFailure("SEARCH_ACCESS_RESTRICTED")
            return web_search.SearchPage(
                provider=self.name,
                search_url="https://search.example.com/?q=query",
                fetched_at=utc_now(),
                response_sha256="a" * 64,
                results=(
                    SearchHit(
                        id="search-controlled",
                        rank=1,
                        title="Controlled result",
                        url="https://www.example.com/official",
                        snippet="An unverified search lead",
                    ),
                ),
            )

    original_tool = web_search.WebSearchTool
    monkeypatch.setattr(
        web_search,
        "WebSearchTool",
        lambda *, ledger: original_tool(ledger=ledger, provider=Provider()),
    )
    ledger_path = tmp_path / "search.sqlite3"
    command = [
        "search",
        "query",
        "--search-ledger",
        str(ledger_path),
    ]
    outputs = []
    for attempt in range(2):
        assert main(command) == (1 if restricted else 0)
        output = capsys.readouterr()
        outputs.append(json.loads(output.out))
        if attempt == 0:
            identities = dict(line.split("=", 1) for line in output.err.splitlines())
            task_id = UUID(identities["search_task_id"])
            request_id = UUID(identities["search_request_id"])
            command.extend(["--task-id", str(task_id), "--request-id", str(request_id)])
        assert f"search_task_id={task_id}" in output.err
        assert f"search_request_id={request_id}" in output.err
    assert len(calls) == 1
    assert outputs[0].pop("call_id") != outputs[1].pop("call_id")
    assert outputs[0] == outputs[1]
    assert SearchLedger(ledger_path).usage(task_id) == (1, 30)
    if restricted:
        assert outputs[0]["error"]["code"] == "SEARCH_ACCESS_RESTRICTED"
        assert outputs[0]["data"] is None
    else:
        assert outputs[0]["data"]["evidence_eligible"] is False
        assert len(outputs[0]["data"]["results"]) == 1
        assert outputs[0]["data"]["task_id"] == str(task_id)
    assert not (tmp_path / "projects").exists()
