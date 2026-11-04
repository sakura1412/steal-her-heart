"""Acquisition CLI wiring uses controlled tools/providers, never the public network."""

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from pydantic import Field, HttpUrl

from steal_her_heart.cli import main
from steal_her_heart.schemas import Contract, Text, ToolError, ToolResult
from steal_her_heart.tools import Tool, ToolRegistry


class FetchInput(Contract):
    url: HttpUrl
    request_id: UUID = Field(default_factory=uuid4)


class FetchOutput(Contract):
    url: HttpUrl
    task_id: UUID
    request_id: UUID
    usage: Text = "unvalidated_source"


@pytest.fixture
def controlled_fetch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {"calls": [], "ledgers": [], "stores": []}

    class Ledger:
        def __init__(self, path):
            self.path = Path(path)
            seen["ledgers"].append(self.path)

    class Store:
        def __init__(self, root):
            self.root = Path(root)
            seen["stores"].append(self.root)

    class ControlledTool(Tool):
        name = "web_fetch"
        description = "Controlled page acquisition fixture without network or file writes"
        input_model = FetchInput
        output_model = FetchOutput
        required_capabilities = frozenset({"web_fetch"})

        def __init__(self, *, ledger, snapshots):
            self.ledger, self.snapshots = ledger, snapshots

        async def run(self, arguments, context):
            seen["calls"].append((arguments, context))
            return FetchOutput(
                url=arguments.url, task_id=context.task_id, request_id=arguments.request_id
            )

    fetch_module = ModuleType("steal_her_heart.web_fetch")
    fetch_module.FetchLedger = Ledger
    fetch_module.WebFetchTool = ControlledTool
    snapshots_module = ModuleType("steal_her_heart.source_snapshots")
    snapshots_module.SnapshotStore = Store
    monkeypatch.setitem(sys.modules, fetch_module.__name__, fetch_module)
    monkeypatch.setitem(sys.modules, snapshots_module.__name__, snapshots_module)
    return seen


def test_fetch_cli_forwards_ids_capability_deadline_and_lazy_default_storage(
    controlled_fetch, tmp_path, capsys
):
    assert main(["fetch", "https://example.com/product"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["tool"] == "web_fetch" and payload["status"] == "ok"
    assert payload["data"]["usage"] == "unvalidated_source"
    arguments, context = controlled_fetch["calls"][0]
    assert str(arguments.url) == "https://example.com/product"
    assert context.capabilities == frozenset({"web_fetch"}) and context.timeout_s == 15
    assert f"fetch_task_id={context.task_id}" in output.err
    assert f"fetch_request_id={arguments.request_id}" in output.err
    assert controlled_fetch["ledgers"] == [Path(".steal-her-heart/fetch-calls.sqlite3")]
    assert controlled_fetch["stores"] == [Path(".steal-her-heart/sources")]
    assert not (tmp_path / "projects").exists()
    assert not (tmp_path / ".steal-her-heart").exists()


def test_fetch_cli_forwards_explicit_id_and_storage_arguments(controlled_fetch, tmp_path, capsys):
    task_id, request_id = uuid4(), uuid4()
    ledger, sources = tmp_path / "fetch.sqlite3", tmp_path / "snapshots"
    assert (
        main(
            [
                "fetch",
                "https://example.com/a",
                "--task-id",
                str(task_id),
                "--request-id",
                str(request_id),
                "--fetch-ledger",
                str(ledger),
                "--sources-root",
                str(sources),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    arguments, context = controlled_fetch["calls"][0]
    assert context.task_id == task_id and arguments.request_id == request_id
    assert controlled_fetch["ledgers"] == [ledger] and controlled_fetch["stores"] == [sources]
    assert json.loads(output.out)["data"]["request_id"] == str(request_id)
    assert f"fetch_task_id={task_id}" in output.err


@pytest.mark.parametrize("url", ["not-a-url", "", "file:///etc/passwd"])
def test_invalid_fetch_arguments_do_not_execute_the_controlled_tool(controlled_fetch, capsys, url):
    assert main(["fetch", url]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "INVALID_ARGUMENT"
    assert controlled_fetch["calls"] == []


def test_fetch_access_restriction_has_error_exit_and_available_ids(
    controlled_fetch, monkeypatch, capsys
):
    calls = []

    async def restricted(self, name, arguments, context):
        assert name == "web_fetch"
        calls.append((arguments["request_id"], context.task_id))
        return ToolResult(
            tool=name,
            status="error",
            error=ToolError(code="FETCH_ACCESS_RESTRICTED", message="Page access is restricted"),
        )

    monkeypatch.setattr(ToolRegistry, "invoke", restricted)
    assert main(["fetch", "https://example.com/"]) == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["status"] == "error" and payload["data"] is None
    assert payload["error"]["code"] == "FETCH_ACCESS_RESTRICTED"
    request_id, task_id = calls[0]
    assert isinstance(request_id, UUID)
    assert f"fetch_task_id={task_id}" in output.err
    assert f"fetch_request_id={request_id}" in output.err
    assert controlled_fetch["calls"] == []


@pytest.mark.parametrize("command", [["hello"], ["/analyze", "任意产品"], ["search", ""]])
def test_other_commands_do_not_initialize_acquisition_storage(controlled_fetch, capsys, command):
    assert main(command) in (0, 2, 3)
    capsys.readouterr()
    assert controlled_fetch["calls"] == []
    assert controlled_fetch["ledgers"] == [] and controlled_fetch["stores"] == []


def test_fetch_does_not_require_or_load_a_broken_project_selection(controlled_fetch, capsys):
    assert main(["--project-name", "../invalid", "fetch", "https://example.com/"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@pytest.mark.parametrize("option", ["--task-id", "--request-id"])
def test_invalid_fetch_identity_is_rejected_before_construction(controlled_fetch, capsys, option):
    with pytest.raises(SystemExit) as caught:
        main(["fetch", "https://example.com/", option, "not-a-uuid"])
    assert caught.value.code == 2
    assert controlled_fetch["ledgers"] == [] and controlled_fetch["stores"] == []
    capsys.readouterr()


@pytest.mark.parametrize("restricted", [False, True])
def test_real_fetch_tool_cli_replays_receipt_or_failure_without_network(
    monkeypatch, tmp_path, capsys, restricted
):
    from steal_her_heart import web_fetch
    from steal_her_heart.schemas import utc_now
    from steal_her_heart.source_snapshots import SnapshotStore

    monkeypatch.chdir(tmp_path)
    calls = []
    body = b"<html><title>Fixture</title><body><p>Controlled source text.</p></body></html>"

    class Provider:
        name = "controlled"

        async def fetch(self, request, *, timeout_s):
            calls.append(request)
            assert 0 < timeout_s <= 15
            if restricted:
                raise web_fetch.FetchFailure("FETCH_ACCESS_RESTRICTED")
            return web_fetch.FetchedPage(
                url=request.url,
                media_type="text/html",
                charset="utf-8",
                body=body,
                fetched_at=utc_now(),
            )

    original_tool = web_fetch.WebFetchTool
    monkeypatch.setattr(
        web_fetch,
        "WebFetchTool",
        lambda *, ledger, snapshots: original_tool(
            ledger=ledger, snapshots=snapshots, provider=Provider()
        ),
    )
    ledger, sources = tmp_path / "fetch.sqlite3", tmp_path / "sources"
    command = [
        "fetch",
        "https://example.com/product",
        "--fetch-ledger",
        str(ledger),
        "--sources-root",
        str(sources),
    ]
    outputs = []
    for attempt in range(2):
        assert main(command) == (1 if restricted else 0)
        output = capsys.readouterr()
        outputs.append(json.loads(output.out))
        if attempt == 0:
            ids = dict(line.split("=", 1) for line in output.err.splitlines())
            task_id, request_id = UUID(ids["fetch_task_id"]), UUID(ids["fetch_request_id"])
            command.extend(["--task-id", str(task_id), "--request-id", str(request_id)])
        assert f"fetch_task_id={task_id}" in output.err
        assert f"fetch_request_id={request_id}" in output.err
    assert len(calls) == 1
    assert outputs[0].pop("call_id") != outputs[1].pop("call_id")
    assert outputs[0] == outputs[1]
    assert web_fetch.FetchLedger(ledger).usage(task_id) == (1, 60)
    if restricted:
        assert outputs[0]["error"]["code"] == "FETCH_ACCESS_RESTRICTED"
        assert outputs[0]["data"] is None and not sources.exists()
    else:
        data = outputs[0]["data"]
        assert data["facts_validated"] is False
        assert "Controlled source text." in data["text_preview"]
        source = data["document"]["source"]
        assert source["published_at"] is None
        assert source["content_sha256"] == hashlib.sha256(body).hexdigest()
        snapshot = SnapshotStore(sources).load(source["snapshot_ref"])
        assert snapshot.raw == body and snapshot.document.source.url == calls[0].url
    assert not (tmp_path / "projects").exists()
