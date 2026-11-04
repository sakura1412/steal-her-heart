"""Candidate CLI reads controlled snapshots; no public network or model is used."""

import hashlib
import json
import socket
from pathlib import Path
from uuid import UUID

import pytest

from steal_her_heart.cli import main
from steal_her_heart.schemas import utc_now
from steal_her_heart.source_snapshots import SnapshotStore
from steal_her_heart.tools import ToolRegistry


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    store = SnapshotStore(tmp_path / "sources")
    document = store.save(
        url="https://example.com/product",
        media_type="text/plain",
        charset="utf-8",
        body="功能 A\n用户反馈待核验 🚀\n".encode(),
        fetched_at=utc_now(),
    )

    def no_network(*args, **kwargs):
        pytest.fail("Candidate binding must not call the network")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    return store, document


def command(document, root=Path("sources"), *, digest=None, span="0:4"):
    return [
        "extract-candidates",
        document.source.snapshot_ref,
        "--text-sha256",
        digest or document.text_sha256,
        "--competitor",
        "任意行业产品",
        "--dimension",
        "能力与体验",
        "--span",
        span,
        "--sources-root",
        str(root),
    ]


def disk_state(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_real_candidate_cli_is_readonly_exact_and_deterministic(source, tmp_path, capsys):
    store, document = source
    before = disk_state(tmp_path)
    outputs = []
    for _ in range(2):
        assert main(command(document, store.root)) == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["tool"] == "extract_features" and result["status"] == "ok"
        data = result["data"]
        assert data["status"] == "candidates_only" and data["facts_validated"] is False
        assert f"extraction_task_id={UUID(data['task_id'])}" in captured.err
        candidate = data["candidates"][0]
        assert candidate["fact"] == candidate["quote"] == "功能 A"
        assert candidate["support_status"] == "unvalidated"
        assert candidate["locator"]["start"] == 0 and candidate["locator"]["end"] == 4
        assert candidate["locator"]["text_sha256"] == document.text_sha256
        assert candidate["source"]["fetched_at"] == document.source.fetched_at.isoformat().replace(
            "+00:00", "Z"
        )
        assert "evidence" not in data
        outputs.append(candidate)
    assert outputs[0] == outputs[1] and disk_state(tmp_path) == before
    assert not (tmp_path / "projects").exists() and not (tmp_path / ".steal-her-heart").exists()


def test_candidate_cli_forwards_multiple_spans_capability_and_deadline(source, monkeypatch, capsys):
    _, document = source
    seen = []
    original = ToolRegistry.invoke

    async def observe(self, name, arguments, context):
        seen.append((name, arguments, context))
        return await original(self, name, arguments, context)

    monkeypatch.setattr(ToolRegistry, "invoke", observe)
    assert main(command(document) + ["--span", "5:9"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["data"]["candidates"]) == 2
    name, arguments, context = seen[0]
    assert name == "extract_features"
    assert arguments["spans"] == [{"start": 0, "end": 4}, {"start": 5, "end": 9}]
    assert context.capabilities == frozenset({"evidence_extraction"}) and context.timeout_s == 5


@pytest.mark.parametrize("span", ["x:5", "1", "1:2:3", "-1:3", "１:３"])
def test_bad_span_syntax_stops_before_snapshot_loading(source, monkeypatch, capsys, span):
    _, document = source

    def no_load(*args):
        pytest.fail("Invalid CLI syntax cannot load a snapshot")

    monkeypatch.setattr(SnapshotStore, "load", no_load)
    with pytest.raises(SystemExit) as caught:
        main(command(document, span=span))
    assert caught.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("span", ["4:4", "5:3", "0:4001"])
def test_invalid_span_schema_returns_argument_error_without_read(source, monkeypatch, capsys, span):
    _, document = source

    def no_load(*args):
        pytest.fail("Invalid argument schema cannot load a snapshot")

    monkeypatch.setattr(SnapshotStore, "load", no_load)
    assert main(command(document, span=span)) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "INVALID_ARGUMENT"
    assert "extraction_task_id=" in captured.err


def test_wrong_text_version_is_execution_error_and_readonly(source, tmp_path, capsys):
    _, document = source
    before = disk_state(tmp_path)
    assert main(command(document, digest="0" * 64)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "EXTRACT_SOURCE_MISMATCH" and result["data"] is None
    assert disk_state(tmp_path) == before


def test_missing_store_is_not_created_by_candidate_cli(source, tmp_path, capsys):
    _, document = source
    missing = tmp_path / "missing"
    assert main(command(document, missing)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "EXTRACT_SNAPSHOT_UNAVAILABLE"
    assert not missing.exists()


def test_candidate_cli_bypasses_project_selection_without_fabricating_a_project(source, capsys):
    _, document = source
    assert main(["--project-name", "../invalid"] + command(document)) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "candidates_only"


def test_other_commands_do_not_load_candidate_tool(source, monkeypatch, capsys):
    from steal_her_heart import evidence_extraction

    def no_tool(*args, **kwargs):
        pytest.fail("Unrelated commands must not construct the candidate tool")

    monkeypatch.setattr(evidence_extraction, "EvidenceExtractionTool", no_tool)
    assert main(["hello"]) == 0
    assert main(["/analyze", "任意产品"]) == 3
    capsys.readouterr()
