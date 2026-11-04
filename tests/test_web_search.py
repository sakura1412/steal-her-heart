"""Search execution tests; all providers, sockets and subprocess I/O are controlled."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import sys
from email.message import Message
from types import SimpleNamespace
from uuid import uuid4

import pytest

from steal_her_heart import _search_http, web_search
from steal_her_heart.schemas import ToolContext, utc_now
from steal_her_heart.search_html import SearchHit
from steal_her_heart.search_ledger import SearchLedger
from steal_her_heart.tools import ToolRegistry
from steal_her_heart.web_search import SearchFailure, SearchPage, WebSearchTool


class Provider:
    name = "fixture"

    def __init__(self, failure=None, delay=0):
        self.calls, self.failure, self.delay = 0, failure, delay

    async def search(self, request, *, timeout_s):
        self.calls += 1
        assert timeout_s > 0
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return SearchPage(
            provider=self.name,
            search_url="https://example.com/search",
            fetched_at=utc_now(),
            response_sha256="a" * 64,
            results=(
                SearchHit(
                    id="hit",
                    rank=1,
                    title="A result",
                    url="https://example.com/product",
                    snippet="A discovery lead",
                ),
            ),
        )


def setup(tmp_path, provider=None, *, limit=30):
    ledger = SearchLedger(tmp_path / "state/search.sqlite3")
    provider = provider or Provider()
    registry = ToolRegistry()
    registry.register(WebSearchTool(ledger=ledger, provider=provider, max_calls=limit))
    context = ToolContext(task_id=uuid4(), capabilities=frozenset({"web_search"}), timeout_s=1)
    return registry, ledger, provider, context


def invoke(registry, context, **arguments):
    return asyncio.run(registry.invoke("web_search", arguments, context))


def test_result_is_a_lead_and_replays_after_reopening_without_new_request(tmp_path):
    registry, ledger, provider, context = setup(tmp_path)
    request_id = uuid4()
    first = invoke(registry, context, query="A product", request_id=request_id)
    assert first.status == "ok" and first.data["evidence_eligible"] is False
    assert first.data["task_id"] == str(context.task_id)
    assert first.data["request_id"] == str(request_id)
    reopened, _, _, _ = setup(tmp_path, provider)
    second = invoke(reopened, context, query="A product", request_id=request_id)
    assert second.data == first.data and second.call_id != first.call_id
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 30)


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "two\nqueries"},
        {"query": "\nleading control"},
        {"query": "trailing control\x7f"},
        {"query": "C1\x85control"},
        {"query": "x", "max_results": True},
        {"query": "x", "max_results": 11},
        {"query": "x", "endpoint": "http://127.0.0.1"},
    ],
)
def test_bad_arguments_do_not_reserve_or_request(tmp_path, arguments):
    registry, ledger, provider, context = setup(tmp_path)
    result = invoke(registry, context, **arguments)
    assert result.error.code == "INVALID_ARGUMENT"
    assert provider.calls == 0 and not ledger.path.exists()


def test_missing_capability_prevents_budget_and_network_activity(tmp_path):
    registry, ledger, provider, _ = setup(tmp_path)
    result = invoke(registry, ToolContext(task_id=uuid4()), query="A")
    assert result.error.code == "PERMISSION_DENIED"
    assert provider.calls == 0 and not ledger.path.exists()


def test_restricted_provider_stops_and_replays_failure_without_retry(tmp_path):
    registry, ledger, provider, context = setup(
        tmp_path, Provider(SearchFailure("SEARCH_ACCESS_RESTRICTED"))
    )
    request_id = uuid4()
    for _ in range(2):
        result = invoke(registry, context, query="A", request_id=request_id)
        assert result.error.code == "SEARCH_ACCESS_RESTRICTED" and result.data is None
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 30)


def test_unknown_provider_exception_is_not_exposed_or_persisted(tmp_path):
    registry, ledger, _, context = setup(tmp_path, Provider(RuntimeError("private-secret")))
    result = invoke(registry, context, query="A")
    assert result.error.code == "SEARCH_PROTOCOL_ERROR"
    assert "private-secret" not in result.model_dump_json()
    assert b"private-secret" not in ledger.path.read_bytes()


def test_budget_survives_new_tool_instance(tmp_path):
    registry, ledger, provider, context = setup(tmp_path, limit=1)
    assert invoke(registry, context, query="A").status == "ok"
    reopened, _, _, _ = setup(tmp_path, provider, limit=1)
    assert invoke(reopened, context, query="B").error.code == "SEARCH_BUDGET_EXCEEDED"
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 1)


def test_concurrent_identical_calls_and_changed_requests_do_not_duplicate_network(tmp_path):
    registry, ledger, provider, context = setup(tmp_path, Provider(delay=0.03))
    request_id = uuid4()

    async def run():
        return await asyncio.gather(
            *[
                registry.invoke("web_search", {"query": "A", "request_id": request_id}, context)
                for _ in range(2)
            ]
        )

    results = asyncio.run(run())
    assert sorted(result.status for result in results) == ["error", "ok"]
    assert next(r.error.code for r in results if r.error) == "SEARCH_IN_FLIGHT"
    changed = invoke(registry, context, query="B", request_id=request_id)
    assert changed.error.code == "SEARCH_REQUEST_CONFLICT"
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 30)


def test_corrupted_cache_never_triggers_automatic_resend(tmp_path):
    registry, ledger, provider, context = setup(tmp_path)
    request_id = uuid4()
    assert invoke(registry, context, query="A", request_id=request_id).status == "ok"
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE search_calls SET response_json=?", ('{"bad":true}',))
    assert (
        invoke(registry, context, query="A", request_id=request_id).error.code
        == "SEARCH_CACHE_INVALID"
    )
    assert provider.calls == 1


def test_registry_timeout_records_cancelled_attempt_and_never_resends_it(tmp_path):
    registry, ledger, provider, context = setup(tmp_path, Provider(delay=1))
    context = ToolContext(
        task_id=context.task_id, capabilities=context.capabilities, timeout_s=0.08
    )
    request_id = uuid4()
    assert invoke(registry, context, query="A", request_id=request_id).error.code == "TOOL_TIMEOUT"
    assert (
        invoke(registry, context, query="A", request_id=request_id).error.code == "SEARCH_CANCELLED"
    )
    assert provider.calls == 1 and ledger.usage(context.task_id) == (1, 30)


class Process:
    def __init__(self, response=None, delay=0):
        self.response = response or {"ok": True, "body_base64": base64.b64encode(b"page").decode()}
        self.delay, self.returncode = delay, None
        self.killed, self.waited, self.input = False, False, None
        self.output = json.dumps(self.response).encode()
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

    def kill(self):
        self.killed, self.returncode = True, -9

    async def wait(self):
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def fake_spawn(monkeypatch, process):
    async def spawn(*args, **kwargs):
        assert args[1].endswith("_search_http.py") and "shell" not in kwargs
        return process

    monkeypatch.setattr(web_search.asyncio, "create_subprocess_exec", spawn)


def test_http_worker_passes_data_without_shell_and_reaps(monkeypatch):
    process = Process()
    fake_spawn(monkeypatch, process)
    assert asyncio.run(web_search._http_worker('a "quoted" query', timeout_s=1)) == b"page"
    assert process.input["query"] == 'a "quoted" query'
    assert process.waited and not process.killed


def test_maximum_unicode_query_uses_bounded_utf8_json(monkeypatch):
    process = Process()
    fake_spawn(monkeypatch, process)
    query = "🚀" * 512
    assert asyncio.run(web_search._http_worker(query, timeout_s=1)) == b"page"
    assert process.input["query"] == query


def test_process_creation_has_a_cancellable_deadline(monkeypatch):
    cancelled = []

    async def spawn(*args, **kwargs):
        try:
            await asyncio.sleep(10)
            pytest.fail("Creation must not outlive the deadline")
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(web_search.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(SearchFailure) as caught:
        asyncio.run(web_search._http_worker("A", timeout_s=0.01))
    assert caught.value.code == "SEARCH_TIMEOUT" and cancelled == [True]


def test_excessive_stdout_is_stopped_and_actual_offline_child_is_reaped(monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    children = []

    async def spawn(*args, **kwargs):
        child = await original_spawn(
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.buffer.write(b'x'*8000000);"
            "sys.stdout.flush();time.sleep(10)",
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(web_search.asyncio, "create_subprocess_exec", spawn)

    async def run():
        with pytest.raises(SearchFailure) as caught:
            await asyncio.wait_for(web_search._http_worker("A", timeout_s=2, max_bytes=1), 3)
        assert caught.value.code == "SEARCH_PROTOCOL_ERROR"

    asyncio.run(run())
    assert len(children) == 1 and children[0].returncode is not None
    assert children[0].returncode != 0


@pytest.mark.parametrize(
    "response,code",
    [
        ({"ok": False, "code": "SEARCH_ACCESS_RESTRICTED"}, "SEARCH_ACCESS_RESTRICTED"),
        ({"ok": False, "code": "untrusted-body"}, "SEARCH_PROTOCOL_ERROR"),
        ({"ok": True, "body_base64": "%%%"}, "SEARCH_PROTOCOL_ERROR"),
        ({"ok": True, "body_base64": "cGFnZQ==", "extra": "bad"}, "SEARCH_PROTOCOL_ERROR"),
        ({"ok": True, "body_base64": "cGFnZQ=="}, "SEARCH_RESPONSE_TOO_LARGE"),
    ],
)
def test_worker_protocol_failure_and_size_limits(monkeypatch, response, code):
    process = Process(response)
    fake_spawn(monkeypatch, process)
    with pytest.raises(SearchFailure) as error:
        asyncio.run(web_search._http_worker("A", timeout_s=1, max_bytes=3))
    assert error.value.code == code and process.waited


@pytest.mark.parametrize("cancel", [False, True])
def test_worker_deadline_and_cancellation_kill_and_reap(monkeypatch, cancel):
    process = Process(delay=1)
    fake_spawn(monkeypatch, process)

    async def run():
        if not cancel:
            with pytest.raises(SearchFailure) as error:
                await web_search._http_worker("A", timeout_s=0.01)
            assert error.value.code == "SEARCH_TIMEOUT"
        else:
            task = asyncio.create_task(web_search._http_worker("A", timeout_s=1))
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert process.killed and process.waited


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["::1"],
        ["93.184.216.34", "10.0.0.1"],
        ["::ffff:127.0.0.1"],
    ],
)
def test_dns_refuses_private_or_mixed_provider_addresses(monkeypatch, addresses):
    monkeypatch.setattr(
        _search_http.socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", (address, 443)) for address in addresses],
    )
    with pytest.raises(_search_http.UnsafeAddress):
        _search_http.public_address(_search_http.HOST)


@pytest.mark.parametrize("proxy", [False, True])
def test_https_connection_pins_target_ip_and_uses_provider_sni(monkeypatch, proxy):
    connected, tls, tunneled = [], [], []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        _search_http.socket, "create_connection", lambda addr, *a: connected.append(addr) or sock
    )
    cls = _search_http.pinned_connection("93.184.216.34")
    conn = cls("user-proxy.example", 7897) if proxy else cls(_search_http.HOST, 443)
    conn._context = SimpleNamespace(
        wrap_socket=lambda s, server_hostname: tls.append(server_hostname) or sock
    )
    if proxy:
        conn.set_tunnel(_search_http.HOST, 443)
        conn._tunnel = lambda: tunneled.append((conn._tunnel_host, conn._tunnel_port))
    conn.connect()
    assert connected == [("user-proxy.example", 7897) if proxy else ("93.184.216.34", 443)]
    assert tls == [_search_http.HOST]
    assert tunneled == ([("93.184.216.34", 443)] if proxy else [])
    conn.close()


class Response:
    def __init__(self, status=200, body=b"<html></html>"):
        self.status, self.body, self.reads = status, body, 0
        self.headers = Message()
        self.headers["Content-Type"] = "text/html"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        self.reads += 1
        return self.body[:limit]


@pytest.mark.parametrize("status", [202, 401, 403, 407, 429, 301, 302, 307, 308, 500])
def test_restricted_and_redirect_http_stops_without_body_or_retry(monkeypatch, status):
    response, requests = Response(status), []
    monkeypatch.setattr(_search_http, "public_address", lambda host: "93.184.216.34")

    def open(request, timeout):
        requests.append(request)
        return response

    monkeypatch.setattr(_search_http, "build_opener", lambda *h: SimpleNamespace(open=open))
    output = _search_http.fetch("A", 1, 100)
    expected = (
        "SEARCH_REDIRECT_BLOCKED"
        if 300 <= status < 400
        else "SEARCH_ACCESS_RESTRICTED"
        if status in {202, 401, 403, 407, 429}
        else "SEARCH_HTTP_ERROR"
    )
    assert output == {"ok": False, "code": expected}
    assert len(requests) == 1 and response.reads == 0
    assert (
        _search_http.NoRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1")
        is None
    )


def test_response_body_is_capped_without_content_length(monkeypatch):
    response = Response(body=b"x" * 101)
    monkeypatch.setattr(_search_http, "public_address", lambda host: "93.184.216.34")
    monkeypatch.setattr(
        _search_http, "build_opener", lambda *h: SimpleNamespace(open=lambda *a, **kw: response)
    )
    assert _search_http.fetch("A", 1, 100)["code"] == "SEARCH_RESPONSE_TOO_LARGE"
    assert response.reads == 1


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            b'<div class="result"><h2><a class="result__a" '
            b'href="https://example.com/product">Product</a></h2>'
            b'<a class="result__snippet">Unverified summary</a></div>',
            1,
        ),
        (b'<div class="no-results">No results found.</div>', 0),
        (b'<form id="challenge-form"></form>', "SEARCH_ACCESS_RESTRICTED"),
        (b"<html>Unknown provider layout</html>", "SEARCH_FORMAT_ERROR"),
    ],
)
def test_real_provider_contract_uses_only_controlled_offline_html(monkeypatch, body, expected):
    calls = []

    async def worker(query, *, timeout_s):
        calls.append((query, timeout_s))
        return body

    monkeypatch.setattr(web_search, "_http_worker", worker)
    provider = web_search.DuckDuckGoSearchProvider()
    request = web_search.SearchRequest(query="产品 & service")
    if isinstance(expected, str):
        with pytest.raises(SearchFailure) as caught:
            asyncio.run(provider.search(request, timeout_s=1))
        assert caught.value.code == expected
    else:
        page = asyncio.run(provider.search(request, timeout_s=1))
        assert len(page.results) == expected
        assert page.provider == "duckduckgo_html"
        assert str(page.search_url).startswith(web_search.ENDPOINT + "?q=")
        assert page.response_sha256 == hashlib.sha256(body).hexdigest()
        assert page.fetched_at.tzinfo is not None
    assert calls == [(request.query, 1)]


@pytest.mark.parametrize(
    "header,value,code",
    [
        ("Content-Type", "application/json", "SEARCH_PROTOCOL_ERROR"),
        ("Content-Encoding", "gzip", "SEARCH_PROTOCOL_ERROR"),
        ("Content-Length", "101", "SEARCH_RESPONSE_TOO_LARGE"),
        ("Content-Length", "invalid", "SEARCH_RESPONSE_TOO_LARGE"),
    ],
)
def test_response_headers_are_checked_before_reading_body(monkeypatch, header, value, code):
    response = Response()
    if header in response.headers:
        response.headers.replace_header(header, value)
    else:
        response.headers.add_header(header, value)
    monkeypatch.setattr(_search_http, "public_address", lambda host: "93.184.216.34")
    monkeypatch.setattr(
        _search_http, "build_opener", lambda *h: SimpleNamespace(open=lambda *a, **kw: response)
    )
    assert _search_http.fetch("A", 1, 100) == {"ok": False, "code": code}
    assert response.reads == 0
