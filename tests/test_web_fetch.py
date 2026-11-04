"""Fetch tool integration with controlled pages and local subprocesses; no live network."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from steal_her_heart import web_fetch
from steal_her_heart.schemas import ToolContext, utc_now
from steal_her_heart.search_ledger import SearchLedger, SearchLedgerError
from steal_her_heart.source_snapshots import SnapshotError, SnapshotStore
from steal_her_heart.tools import ToolRegistry
from steal_her_heart.web_fetch import (
    FetchedPage,
    FetchFailure,
    FetchLedger,
    FetchRequest,
    PublicFetchProvider,
    WebFetchTool,
)


class Provider:
    name = "fixture"

    def __init__(
        self,
        *,
        body=b"Product\nPrice unknown",
        media_type="text/plain",
        failure=None,
        delay=0,
        other_url=None,
    ):
        self.body, self.media_type, self.failure = body, media_type, failure
        self.delay, self.other_url, self.calls = delay, other_url, 0

    async def fetch(self, request, *, timeout_s):
        self.calls += 1
        assert timeout_s > 0
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return FetchedPage(
            url=self.other_url or request.url,
            body=self.body,
            media_type=self.media_type,
            charset="utf-8",
            fetched_at=utc_now(),
        )


def setup(tmp_path, provider=None, *, limit=60):
    ledger = FetchLedger(tmp_path / "fetch.sqlite3")
    snapshots = SnapshotStore(tmp_path / "sources")
    provider = provider or Provider()
    registry = ToolRegistry()
    registry.register(
        WebFetchTool(ledger=ledger, snapshots=snapshots, provider=provider, max_calls=limit)
    )
    context = ToolContext(task_id=uuid4(), capabilities=frozenset({"web_fetch"}), timeout_s=1)
    return registry, ledger, snapshots, provider, context


def invoke(registry, context, **arguments):
    return asyncio.run(registry.invoke("web_fetch", arguments, context))


def test_success_is_a_source_with_reopenable_snapshot_and_local_replay(tmp_path):
    registry, ledger, snapshots, provider, context = setup(tmp_path)
    request_id = uuid4()
    first = invoke(registry, context, url="https://example.com/product", request_id=request_id)
    assert first.status == "ok" and first.data["facts_validated"] is False
    assert "evidence" not in first.data
    source = first.data["document"]["source"]
    assert source["content_sha256"] == hashlib.sha256(provider.body).hexdigest()
    assert source["published_at"] is None
    assert snapshots.load(source["snapshot_ref"]).raw == provider.body
    reopened, _, _, _, _ = setup(tmp_path, provider)
    again = invoke(reopened, context, url="https://example.com/product", request_id=request_id)
    assert again.data == first.data and again.call_id != first.call_id
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "http://127.0.0.1/"},
        {"url": "http://127.1/"},
        {"url": "http://169.254.169.254/"},
        {"url": "http://[::1]/"},
        {"url": "http://user:pass@example.com/"},
        {"url": "https://example.com:8080/"},
        {"url": "file:///etc/passwd"},
        {"url": "http://localhost/"},
        {"url": "http://printer.local/"},
        {"url": "https://example.com/\n"},
        {"url": "https://example.com/", "max_bytes": 99999999},
        {"url": True},
        {"url": "https://example.com/" + "🚀" * 512},
    ],
)
def test_invalid_input_never_reserves_or_acquires(tmp_path, arguments):
    registry, ledger, _, provider, context = setup(tmp_path)
    result = invoke(registry, context, **arguments)
    assert result.error.code == "INVALID_ARGUMENT"
    assert provider.calls == 0 and not ledger.path.exists()
    assert not (tmp_path / "sources").exists()


def test_public_ipv6_url_is_allowed_locally_without_dns():
    assert FetchRequest(url="https://[2001:4860:4860::8888]/").url.host


def test_permission_denied_has_no_filesystem_or_provider_effect(tmp_path):
    registry, ledger, _, provider, _ = setup(tmp_path)
    result = invoke(registry, ToolContext(task_id=uuid4()), url="https://example.com/")
    assert result.error.code == "PERMISSION_DENIED"
    assert provider.calls == 0 and not ledger.path.exists()


def test_url_fragment_does_not_make_a_second_acquisition(tmp_path):
    registry, ledger, _, provider, context = setup(tmp_path)
    request_id = uuid4()
    first = invoke(registry, context, url="https://example.com/#a", request_id=request_id)
    second = invoke(registry, context, url="https://example.com/#b", request_id=request_id)
    assert first.status == second.status == "ok" and first.data == second.data
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


@pytest.mark.parametrize("failure", [True, False])
def test_restriction_from_transport_or_html_is_cached_as_failure(tmp_path, failure):
    provider = (
        Provider(failure=FetchFailure("FETCH_ACCESS_RESTRICTED"))
        if failure
        else Provider(body=b'<form id="challenge-form"></form>', media_type="text/html")
    )
    registry, ledger, _, _, context = setup(tmp_path, provider)
    request_id = uuid4()
    for _ in range(2):
        result = invoke(registry, context, url="https://example.com/", request_id=request_id)
        assert result.error.code == "FETCH_ACCESS_RESTRICTED" and result.data is None
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


def test_provider_error_does_not_leak_or_persist_raw_details(tmp_path):
    registry, ledger, _, _, context = setup(tmp_path, Provider(failure=RuntimeError("secret")))
    result = invoke(registry, context, url="https://example.com/")
    assert result.error.code == "FETCH_PROTOCOL_ERROR"
    assert "secret" not in result.model_dump_json() and b"secret" not in ledger.path.read_bytes()


def test_empty_http_body_is_no_text_rather_than_a_successful_source(tmp_path):
    registry, _, _, provider, context = setup(tmp_path, Provider(body=b""))
    result = invoke(registry, context, url="https://example.com/")
    assert result.error.code == "FETCH_NO_TEXT" and result.data is None
    assert provider.calls == 1 and not (tmp_path / "sources").exists()


def test_provider_cannot_silently_redirect_the_source_url(tmp_path):
    registry, _, _, provider, context = setup(tmp_path, Provider(other_url="https://example.org/"))
    result = invoke(registry, context, url="https://example.com/")
    assert result.error.code == "FETCH_PROTOCOL_ERROR" and provider.calls == 1
    assert not (tmp_path / "sources").exists()


def test_budget_and_inflight_intents_survive_tool_recreation(tmp_path):
    registry, ledger, _, provider, context = setup(tmp_path, limit=1)
    assert invoke(registry, context, url="https://example.com/").status == "ok"
    reopened, _, _, _, _ = setup(tmp_path, provider, limit=1)
    assert (
        invoke(reopened, context, url="https://example.org/").error.code == "FETCH_BUDGET_EXCEEDED"
    )
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 1)


def test_search_and_fetch_ledgers_refuse_each_others_database(tmp_path):
    path, task_id, request_id = tmp_path / "calls.sqlite3", uuid4(), uuid4()
    SearchLedger(path).reserve(task_id=task_id, request_id=request_id, fingerprint="a" * 64)
    before = path.read_bytes()
    provider, registry = Provider(), ToolRegistry()
    registry.register(
        WebFetchTool(
            ledger=FetchLedger(path), snapshots=SnapshotStore(tmp_path / "s"), provider=provider
        )
    )
    result = invoke(
        registry,
        ToolContext(task_id=task_id, capabilities=frozenset({"web_fetch"})),
        url="https://example.com/",
    )
    assert result.error.code == "FETCH_LEDGER_SCHEMA"
    assert provider.calls == 0 and path.read_bytes() == before

    other = tmp_path / "fetch-only.sqlite3"
    FetchLedger(other).reserve(
        task_id=task_id, request_id=request_id, fingerprint="b" * 64, max_calls=60
    )
    before = other.read_bytes()
    with pytest.raises(SearchLedgerError) as caught:
        SearchLedger(other).reserve(task_id=task_id, request_id=uuid4(), fingerprint="c" * 64)
    assert caught.value.code == "SEARCH_LEDGER_SCHEMA" and other.read_bytes() == before


def test_unavailable_snapshot_platform_stops_before_budget_or_network(tmp_path, monkeypatch):
    registry, ledger, snapshots, provider, context = setup(tmp_path)

    def unsupported():
        raise SnapshotError("FETCH_SNAPSHOT_UNAVAILABLE")

    monkeypatch.setattr(snapshots, "check_support", unsupported)
    result = invoke(registry, context, url="https://example.com/")
    assert result.error.code == "FETCH_SNAPSHOT_UNAVAILABLE"
    assert provider.calls == 0 and not ledger.path.exists()


def test_concurrent_same_request_only_fetches_once(tmp_path):
    registry, ledger, _, provider, context = setup(tmp_path, Provider(delay=0.03))
    request_id = uuid4()

    async def run():
        return await asyncio.gather(
            *[
                registry.invoke(
                    "web_fetch", {"url": "https://example.com/", "request_id": request_id}, context
                )
                for _ in range(2)
            ]
        )

    results = asyncio.run(run())
    assert sorted(result.status for result in results) == ["error", "ok"]
    assert next(result.error.code for result in results if result.error) == "FETCH_IN_FLIGHT"
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


def test_cancellation_with_failed_error_commit_keeps_uncertain_intent(tmp_path, monkeypatch):
    registry, ledger, _, provider, context = setup(tmp_path, Provider(delay=1))
    context = ToolContext(
        task_id=context.task_id, capabilities=context.capabilities, timeout_s=0.08
    )

    def broken_commit(*args):
        raise SearchLedgerError("SEARCH_LEDGER_UNAVAILABLE", "Safe error")

    monkeypatch.setattr(ledger, "finish_error", broken_commit)
    request_id = uuid4()
    first = invoke(registry, context, url="https://example.com/", request_id=request_id)
    again = invoke(registry, context, url="https://example.com/", request_id=request_id)
    assert first.error.code == "TOOL_TIMEOUT" and again.error.code == "FETCH_IN_FLIGHT"
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


def test_corrupt_snapshot_or_receipt_never_triggers_another_download(tmp_path):
    registry, ledger, _, provider, context = setup(tmp_path)
    request_id = uuid4()
    assert (
        invoke(registry, context, url="https://example.com/", request_id=request_id).status == "ok"
    )
    raw_file = next((tmp_path / "sources").rglob("raw.bin"))
    raw_file.write_bytes(b"tampered")
    result = invoke(registry, context, url="https://example.com/", request_id=request_id)
    assert result.error.code == "FETCH_CACHE_INVALID" and provider.calls == 1
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE search_calls SET response_json=?", ('{"bad":true}',))
    result = invoke(registry, context, url="https://example.com/", request_id=request_id)
    assert result.error.code == "FETCH_CACHE_INVALID" and provider.calls == 1


def test_preview_preserves_trailing_whitespace_at_truncation_for_replay(tmp_path):
    registry, _, _, provider, context = setup(tmp_path, Provider(body=b"a" * 11999 + b"\nlast"))
    request_id = uuid4()
    first = invoke(registry, context, url="https://example.com/", request_id=request_id)
    assert first.status == "ok" and first.data["text_preview"].endswith("\n")
    assert first.data["text_truncated"] is True
    again = invoke(registry, context, url="https://example.com/", request_id=request_id)
    assert again.data == first.data and provider.calls == 1


def test_timeout_and_concurrent_calls_do_not_resend_unresolved_intents(tmp_path):
    registry, ledger, _, provider, context = setup(tmp_path, Provider(delay=1))
    context = ToolContext(
        task_id=context.task_id, capabilities=context.capabilities, timeout_s=0.08
    )
    request_id = uuid4()
    assert (
        invoke(registry, context, url="https://example.com/", request_id=request_id).error.code
        == "TOOL_TIMEOUT"
    )
    assert (
        invoke(registry, context, url="https://example.com/", request_id=request_id).error.code
        == "FETCH_CANCELLED"
    )
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 60)


def test_public_provider_uses_only_bounded_worker_metadata(monkeypatch):
    async def worker(url, *, timeout_s):
        return {
            "url": url,
            "media_type": "text/plain",
            "charset": "utf-8",
            "body": b"text",
            "fetched_at": utc_now(),
        }

    monkeypatch.setattr(web_fetch, "_fetch_worker", worker)
    result = asyncio.run(
        PublicFetchProvider().fetch(FetchRequest(url="https://example.com/"), timeout_s=1)
    )
    assert result.body == b"text" and result.media_type == "text/plain"


class Process:
    def __init__(self, response, *, delay=0):
        self.output, self.delay = json.dumps(response).encode(), delay
        self.returncode, self.killed, self.waited = None, False, False
        self.stdin = SimpleNamespace(write=self.write, drain=self.drain, close=lambda: None)
        self.stdout = SimpleNamespace(read=self.read)

    def write(self, value):
        self.input = json.loads(value)

    async def drain(self):
        pass

    async def read(self, limit):
        if self.delay and not self.killed:
            await asyncio.sleep(self.delay)
        chunk, self.output = self.output[:limit], self.output[limit:]
        return chunk

    async def wait(self):
        self.waited = True
        self.returncode = self.returncode if self.returncode is not None else 0

    def kill(self):
        self.killed, self.returncode = True, -9


def envelope(**kwargs):
    return {
        "ok": True,
        "url": "https://example.com/",
        "media_type": "text/plain",
        "charset": "utf-8",
        "body_base64": base64.b64encode(b"Product").decode(),
        **kwargs,
    }


def fake_spawn(monkeypatch, process):
    async def spawn(*args, **kwargs):
        assert args[1].endswith("_fetch_http.py") and "shell" not in kwargs
        return process

    monkeypatch.setattr(web_fetch.asyncio, "create_subprocess_exec", spawn)


def test_worker_uses_fixed_script_and_reaps(monkeypatch):
    process = Process(envelope())
    fake_spawn(monkeypatch, process)
    result = asyncio.run(web_fetch._fetch_worker("https://example.com/", timeout_s=1))
    assert result["body"] == b"Product" and process.waited and not process.killed
    assert process.input["url"] == "https://example.com/" and process.input["max_bytes"] == 1048576


@pytest.mark.parametrize(
    "response,code",
    [
        ({"ok": False, "code": "FETCH_ACCESS_RESTRICTED"}, "FETCH_ACCESS_RESTRICTED"),
        ({"ok": False, "code": "secret"}, "FETCH_PROTOCOL_ERROR"),
        (envelope(body_base64="%%%"), "FETCH_PROTOCOL_ERROR"),
        (envelope(extra="bad"), "FETCH_PROTOCOL_ERROR"),
    ],
)
def test_worker_returns_safe_errors_and_reaps(monkeypatch, response, code):
    process = Process(response)
    fake_spawn(monkeypatch, process)
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(web_fetch._fetch_worker("https://example.com/", timeout_s=1))
    assert caught.value.code == code and process.waited


@pytest.mark.parametrize("cancel", [False, True])
def test_worker_deadline_or_external_cancel_kills_and_reaps(monkeypatch, cancel):
    process = Process(envelope(), delay=1)
    fake_spawn(monkeypatch, process)

    async def run():
        task = asyncio.create_task(web_fetch._fetch_worker("https://example.com/", timeout_s=0.01))
        if cancel:
            await asyncio.sleep(0.005)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else FetchFailure):
            await task

    asyncio.run(run())
    assert process.killed and process.waited


def test_real_offline_child_is_killed_when_output_exceeds_limit(monkeypatch):
    original, children = asyncio.create_subprocess_exec, []

    async def spawn(*args, **kwargs):
        child = await original(
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.buffer.write(b'x'*5000000);"
            "sys.stdout.flush();time.sleep(10)",
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(web_fetch.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(web_fetch._fetch_worker("https://example.com/", timeout_s=2))
    assert caught.value.code == "FETCH_PROTOCOL_ERROR"
    assert children[0].returncode is not None and children[0].returncode != 0
