"""HTTP worker boundary tests using only controlled input and fake sockets."""

import base64
import http.client
import io
import json
import socket
import urllib.request
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from steal_her_heart import _search_http


def worker_input(**changes):
    values = {"query": "A product", "timeout_s": 1, "max_bytes": 1024}
    values.update(changes)
    return json.dumps(values, ensure_ascii=False).encode("utf-8")


def run_main(monkeypatch, payload):
    output = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(_search_http.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
        patch.setattr(_search_http.sys, "stdout", output)
        _search_http.main()
    return json.loads(output.getvalue())


def test_main_accepts_full_length_utf8_query_without_truncation(monkeypatch):
    query = "\U0001f680" * 512
    calls = []

    def fetch(*arguments):
        calls.append(arguments)
        return {"ok": True, "body_base64": base64.b64encode(b"page").decode("ascii")}

    monkeypatch.setattr(_search_http, "fetch", fetch)
    payload = worker_input(query=query)
    assert len(payload) < 4097
    assert run_main(monkeypatch, payload) == {"ok": True, "body_base64": "cGFnZQ=="}
    assert calls == [(query, 1, 1024)]


@pytest.mark.parametrize(
    "changes",
    [
        {"query": " "},
        {"query": "x" * 513},
        {"query": "product\x00"},
        {"query": "product\x7f"},
        {"query": "product\x85"},
        {"query": 123},
        {"timeout_s": True},
        {"timeout_s": 0},
        {"timeout_s": 61},
        {"timeout_s": float("nan")},
        {"max_bytes": True},
        {"max_bytes": 0},
        {"max_bytes": _search_http.MAX_BODY + 1},
        {"extra": "field"},
    ],
)
def test_main_rejects_invalid_parameters_without_fetch(monkeypatch, changes):
    def fetch(*arguments):
        pytest.fail("Invalid input must not start a request")

    monkeypatch.setattr(_search_http, "fetch", fetch)
    assert run_main(monkeypatch, worker_input(**changes)) == {
        "ok": False,
        "code": "SEARCH_PROTOCOL_ERROR",
    }


@pytest.mark.parametrize("payload", [b"{", b"[]", b"{}", b"x" * 4098])
def test_main_rejects_malformed_or_oversized_input_without_fetch(monkeypatch, payload):
    def fetch(*arguments):
        pytest.fail("Invalid input must not start a request")

    monkeypatch.setattr(_search_http, "fetch", fetch)
    assert run_main(monkeypatch, payload) == {"ok": False, "code": "SEARCH_PROTOCOL_ERROR"}


@pytest.mark.parametrize(
    "exception,code",
    [
        (_search_http.AccessRestricted("private-secret"), "SEARCH_ACCESS_RESTRICTED"),
        (_search_http.UnsafeAddress("private-secret"), "SEARCH_UNSAFE_ADDRESS"),
        (socket.timeout("private-secret"), "SEARCH_TIMEOUT"),
        (URLError("private-secret"), "SEARCH_NETWORK_ERROR"),
        (OSError("private-secret"), "SEARCH_NETWORK_ERROR"),
        (http.client.BadStatusLine("private-secret"), "SEARCH_NETWORK_ERROR"),
        (RuntimeError("private-secret"), "SEARCH_PROTOCOL_ERROR"),
    ],
)
def test_main_classifies_exceptions_without_exposing_details(monkeypatch, exception, code):
    def fetch(*arguments):
        raise exception

    monkeypatch.setattr(_search_http, "fetch", fetch)
    result = run_main(monkeypatch, worker_input())
    assert result == {"ok": False, "code": code}
    assert "private-secret" not in json.dumps(result)


class ProxySocket:
    def __init__(self, status):
        self.response = io.BytesIO(
            f"HTTP/1.0 {status} private-proxy-reason\r\n"
            "Proxy-Authenticate: private-secret\r\n\r\n".encode("ascii")
        )
        self.sent = []
        self.closed = False

    def sendall(self, value):
        self.sent.append(value)

    def makefile(self, *arguments, **keywords):
        return self.response

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "status,code",
    [
        (202, "SEARCH_ACCESS_RESTRICTED"),
        (401, "SEARCH_ACCESS_RESTRICTED"),
        (403, "SEARCH_ACCESS_RESTRICTED"),
        (407, "SEARCH_ACCESS_RESTRICTED"),
        (429, "SEARCH_ACCESS_RESTRICTED"),
        (400, "SEARCH_NETWORK_ERROR"),
        (500, "SEARCH_NETWORK_ERROR"),
    ],
)
def test_proxy_connect_errors_stop_once_before_tls_and_map_safely(monkeypatch, status, code):
    sock = ProxySocket(status)
    connected, tls, connections = [], [], []

    def connect(address, *arguments):
        connected.append(address)
        return sock

    def open_request(request, timeout):
        connection = _search_http.pinned_connection("93.184.216.34")("proxy.example", 8080)
        connections.append(connection)
        connection.set_tunnel(
            _search_http.HOST, 443, headers={"Proxy-Authorization": "Basic private-secret"}
        )
        connection._context = SimpleNamespace(
            wrap_socket=lambda *args, **kwargs: tls.append((args, kwargs)) or sock
        )
        connection.connect()
        pytest.fail("A rejected CONNECT must not proceed to the search request")

    monkeypatch.setattr(_search_http, "public_address", lambda host: "93.184.216.34")
    monkeypatch.setattr(_search_http.socket, "create_connection", connect)
    monkeypatch.setattr(
        _search_http, "build_opener", lambda *handlers: SimpleNamespace(open=open_request)
    )
    result = run_main(monkeypatch, worker_input())
    assert result == {"ok": False, "code": code}
    assert connected == [("proxy.example", 8080)]
    assert len(sock.sent) == 1 and sock.sent[0].startswith(b"CONNECT 93.184.216.34:443 ")
    assert not tls and sock.closed
    assert connections[0].response_class is http.client.HTTPResponse
    assert "private-" not in json.dumps(result)


def test_successful_proxy_tunnel_restores_normal_response_parser_and_provider_sni(monkeypatch):
    sock = ProxySocket(200)
    connected, tls = [], []
    monkeypatch.setattr(
        _search_http.socket,
        "create_connection",
        lambda address, *args: connected.append(address) or sock,
    )
    connection = _search_http.pinned_connection("93.184.216.34")("proxy.example", 8080)
    connection.set_tunnel(_search_http.HOST, 443)
    connection._context = SimpleNamespace(
        wrap_socket=lambda value, server_hostname: tls.append(server_hostname) or value
    )
    connection.connect()
    assert connected == [("proxy.example", 8080)]
    assert tls == [_search_http.HOST]
    assert connection.response_class is http.client.HTTPResponse
    assert not sock.closed
    connection.close()
    assert sock.closed


@pytest.mark.parametrize("status", [403, 407, 429])
def test_real_urllib_handler_preserves_connect_restriction_classification(monkeypatch, status):
    sock = ProxySocket(status)
    connected = []
    monkeypatch.setattr(_search_http, "public_address", lambda host: "93.184.216.34")
    monkeypatch.setattr(
        _search_http.socket,
        "create_connection",
        lambda address, *args: connected.append(address) or sock,
    )
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    opener = _search_http.build_opener(
        urllib.request.ProxyHandler({"https": "http://proxy.example:8080"}),
        _search_http.NoRedirect(),
        _search_http.PinnedHTTPSHandler("93.184.216.34"),
    )
    monkeypatch.setattr(_search_http, "build_opener", lambda *handlers: opener)
    assert run_main(monkeypatch, worker_input()) == {
        "ok": False,
        "code": "SEARCH_ACCESS_RESTRICTED",
    }
    assert connected == [("proxy.example", 8080)]
    assert len(sock.sent) == 1 and sock.closed
