"""Read-only tool integration: explicit quotes are candidates, never validated facts."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from steal_her_heart.evidence_extraction import EvidenceExtractionTool
from steal_her_heart.schemas import ToolContext
from steal_her_heart.source_snapshots import SnapshotStore
from steal_her_heart.tools import ToolRegistry


def fixture(tmp_path):
    store = SnapshotStore(tmp_path / "sources")
    document = store.save(
        url="https://example.com/service",
        media_type="text/plain",
        charset="utf-8",
        body="服务方案\n按项目报价。\n地区需要确认。".encode(),
        fetched_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    snapshot = store.load(document.source.snapshot_ref)
    registry = ToolRegistry()
    registry.register(EvidenceExtractionTool(snapshots=store))
    context = ToolContext(task_id=uuid4(), capabilities=frozenset({"evidence_extraction"}))
    start = snapshot.text.index("按项目")
    arguments = {
        "snapshot_ref": document.source.snapshot_ref,
        "text_sha256": document.text_sha256,
        "competitor": "某专业服务",
        "dimension": "商业模式",
        "spans": [{"start": start, "end": start + len("按项目报价。")}],
    }
    return store, snapshot, registry, context, arguments


def invoke(registry, context, arguments):
    return asyncio.run(registry.invoke("extract_features", arguments, context))


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_exact_quote_candidates_keep_source_date_and_do_not_write_files(tmp_path):
    _, snapshot, registry, context, arguments = fixture(tmp_path)
    before = files(tmp_path)
    result = invoke(registry, context, arguments)
    assert result.status == "ok" and result.data["status"] == "candidates_only"
    assert result.data["facts_validated"] is False
    candidate = result.data["candidates"][0]
    assert candidate["fact"] == candidate["quote"] == "按项目报价。"
    assert candidate["support_status"] == "unvalidated"
    assert candidate["source"]["fetched_at"].startswith("2024-01-01")
    assert candidate["locator"]["text_sha256"] == snapshot.document.text_sha256
    assert candidate["locator"]["start_line"] == candidate["locator"]["end_line"] == 2
    assert "evidence" not in result.data and "confidence" not in candidate
    assert files(tmp_path) == before


def test_reading_source_in_another_task_does_not_refresh_or_validate_it(tmp_path):
    _, _, registry, context, arguments = fixture(tmp_path)
    first = invoke(registry, context, arguments)
    second = invoke(
        registry, ToolContext(task_id=uuid4(), capabilities=context.capabilities), arguments
    )
    assert first.data["task_id"] != second.data["task_id"]
    assert first.data["candidates"] == second.data["candidates"]
    assert second.data["candidates"][0]["source"]["fetched_at"].startswith("2024-01-01")
    assert second.data["facts_validated"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"snapshot_ref": "../outside"},
        {"snapshot_ref": "sha256:" + "a" * 64 + "\n"},
        {"text_sha256": "a" * 63},
        {"text_sha256": "a" * 64 + "\n"},
        {"competitor": " "},
        {"dimension": " "},
        {"spans": []},
        {"spans": [{"start": True, "end": 5}]},
        {"spans": [{"start": 0.0, "end": 5}]},
        {"spans": [{"start": -1, "end": 5}]},
        {"spans": [{"start": 5, "end": 5}]},
        {"spans": [{"start": 0, "end": 4001}]},
        {"endpoint": "https://example.com"},
    ],
)
def test_invalid_arguments_cannot_read_snapshot(tmp_path, monkeypatch, patch):
    store, _, registry, context, arguments = fixture(tmp_path)

    def denied(*args):
        pytest.fail("Invalid request must not read source storage")

    monkeypatch.setattr(store, "load", denied)
    assert invoke(registry, context, {**arguments, **patch}).error.code == "INVALID_ARGUMENT"


def test_capability_is_required_before_snapshot_read(tmp_path, monkeypatch):
    store, _, registry, _, arguments = fixture(tmp_path)

    def denied(*args):
        pytest.fail("Unauthorized request must not read source storage")

    monkeypatch.setattr(store, "load", denied)
    assert (
        invoke(registry, ToolContext(task_id=uuid4()), arguments).error.code == "PERMISSION_DENIED"
    )


def test_wrong_text_version_and_tampered_source_never_return_partial_candidates(tmp_path):
    _, _, registry, context, arguments = fixture(tmp_path)
    wrong_version = invoke(registry, context, {**arguments, "text_sha256": "a" * 64})
    assert wrong_version.error.code == "EXTRACT_SOURCE_MISMATCH" and wrong_version.data is None
    next((tmp_path / "sources").rglob("raw.bin")).write_bytes(b"tampered")
    corrupt = invoke(registry, context, arguments)
    assert corrupt.error.code == "EXTRACT_SNAPSHOT_CORRUPT" and corrupt.data is None


def test_missing_source_is_reported_without_creating_files(tmp_path):
    registry = ToolRegistry()
    registry.register(EvidenceExtractionTool(snapshots=SnapshotStore(tmp_path / "missing")))
    result = invoke(
        registry,
        ToolContext(task_id=uuid4(), capabilities=frozenset({"evidence_extraction"})),
        {
            "snapshot_ref": "sha256:" + "a" * 64,
            "text_sha256": "b" * 64,
            "competitor": "硬件品牌",
            "dimension": "保修",
            "spans": [{"start": 0, "end": 3}],
        },
    )
    assert result.error.code == "EXTRACT_SNAPSHOT_UNAVAILABLE" and result.data is None
    assert not (tmp_path / "missing").exists()


def test_out_of_bounds_or_duplicate_selection_fails_whole_batch(tmp_path):
    _, snapshot, registry, context, arguments = fixture(tmp_path)
    for extra in [
        arguments["spans"][0],
        {"start": len(snapshot.text), "end": len(snapshot.text) + 1},
    ]:
        result = invoke(registry, context, {**arguments, "spans": arguments["spans"] + [extra]})
        assert result.status == "error" and result.data is None


def test_unexpected_storage_exception_is_not_exposed(tmp_path, monkeypatch):
    store, _, registry, context, arguments = fixture(tmp_path)

    def failed(*args):
        raise RuntimeError("private source details")

    monkeypatch.setattr(store, "load", failed)
    result = invoke(registry, context, arguments)
    assert result.error.code == "TOOL_EXECUTION_ERROR"
    assert "private source details" not in result.model_dump_json()
