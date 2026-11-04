"""Fetch transport boundary tests; all sockets, DNS and responses are controlled."""

import base64
import http.client
import io
import json
import socket
import urllib.request
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from steal_her_heart import _fetch_http as worker


def worker_input(**changes):
    values = {"url": "https://example.com/product", "timeout_s": 1, "max_bytes": 1024}
    values.update(changes)
    return json.dumps(values, ensure_ascii=False).encode("utf-8")


def run_main(monkeypatch, payload):
    output = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
        patch.setattr(worker.sys, "stdout", output)
        worker.main()
    return json.loads(output.getvalue())


@pytest.mark.parametrize(
    "url,expected",
    [
        ("HTTPS://EXAMPLE.COM:443/product#section", "https://example.com/product"),
        ("http://example.com:80", "http://example.com/"),
        ("https://example.com./", "https://example.com/"),
        (
            "https://例子.com/产品?q=服务&x=%20",
            "https://xn--fsqu00a.com/%E4%BA%A7%E5%93%81?q=%E6%9C%8D%E5%8A%A1&x=%20",
        ),
        ("https://[2606:4700:4700::1111]:443/", "https://[2606:4700:4700::1111]/"),
        ("http://93.184.216.34/", "http://93.184.216.34/"),
    ],
)
def test_url_normalization_is_local_and_does_not_resolve(monkeypatch, url, expected):
    monkeypatch.setattr(worker.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("No DNS"))
    assert worker.normalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        " https://example.com",
        "https://example.com/a b",
        "https://example.com/\n",
        "https://example.com/\x7f",
        "https://example.com/\x85",
        "file:///etc/passwd",
        "ftp://example.com/",
        "javascript:alert(1)",
        "//example.com/",
        "https:/example.com/",
        "http://user:secret@example.com/",
        "http://@example.com/",
        "http://example.com:8080/",
        "https://example.com:80/",
        "http://example.com:443/",
        "https://example.com:bad/",
        "http://localhost/",
        "http://service.local/",
        "http://metadata.google.internal/",
        "http://printer/",
        "http://service.test/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://[ff02::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[64:ff9b::a9fe:a9fe]/",
        "http://[2002:7f00:1::]/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://127。0。0。1/",
        "http://[fe80::1%25en0]/",
        "http://example.com\\evil/",
        "https://example.com/%oops",
        "https://example.com/" + "x" * 2048,
    ],
)
def test_unsafe_urls_stop_before_dns_or_fetch(monkeypatch, url):
    monkeypatch.setattr(worker, "fetch", lambda *a: pytest.fail("No fetch for unsafe URL"))
    monkeypatch.setattr(worker.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("No DNS"))
    assert run_main(monkeypatch, worker_input(url=url)) == {
        "ok": False,
        "code": "FETCH_UNSAFE_ADDRESS",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"url": 123},
        {"timeout_s": True},
        {"timeout_s": 0},
        {"timeout_s": 61},
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
        {"max_bytes": True},
        {"max_bytes": 0},
        {"max_bytes": worker.MAX_BODY + 1},
        {"extra": "field"},
    ],
)
def test_invalid_parameters_never_start_fetch(monkeypatch, changes):
    monkeypatch.setattr(worker, "fetch", lambda *a: pytest.fail("No fetch"))
    assert run_main(monkeypatch, worker_input(**changes)) == {
        "ok": False,
        "code": "FETCH_PROTOCOL_ERROR",
    }


@pytest.mark.parametrize("payload", [b"{", b"[]", b"{}", b"x" * (worker.MAX_INPUT + 1)])
def test_malformed_or_oversized_input_never_starts_fetch(monkeypatch, payload):
    monkeypatch.setattr(worker, "fetch", lambda *a: pytest.fail("No fetch"))
    assert run_main(monkeypatch, payload) == {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}


def test_main_preserves_utf8_path_and_normalizes_before_fetch(monkeypatch):
    calls = []

    def fetch(*arguments):
        calls.append(arguments)
        return {"ok": False, "code": "FETCH_ACCESS_RESTRICTED"}

    monkeypatch.setattr(worker, "fetch", fetch)
    assert run_main(monkeypatch, worker_input(url="https://example.com/产品#section")) == {
        "ok": False,
        "code": "FETCH_ACCESS_RESTRICTED",
    }
    assert calls == [("https://example.com/%E4%BA%A7%E5%93%81", 1, 1024)]


@pytest.mark.parametrize(
    "exception,code",
    [
        (worker.AccessRestricted("private-secret"), "FETCH_ACCESS_RESTRICTED"),
        (worker.UnsafeAddress("private-secret"), "FETCH_UNSAFE_ADDRESS"),
        (socket.timeout("private-secret"), "FETCH_TIMEOUT"),
        (URLError(socket.timeout("private-secret")), "FETCH_TIMEOUT"),
        (URLError("private-secret"), "FETCH_NETWORK_ERROR"),
        (OSError("private-secret"), "FETCH_NETWORK_ERROR"),
        (http.client.BadStatusLine("private-secret"), "FETCH_NETWORK_ERROR"),
        (RuntimeError("private-secret"), "FETCH_PROTOCOL_ERROR"),
    ],
)
def test_exception_mapping_does_not_expose_details(monkeypatch, exception, code):
    def fetch(*arguments):
        raise exception

    monkeypatch.setattr(worker, "fetch", fetch)
    result = run_main(monkeypatch, worker_input())
    assert result == {"ok": False, "code": code}
    assert "private-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["::1"],
        ["224.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["64:ff9b::a9fe:a9fe"],
    ],
)
def test_dns_refuses_any_private_reserved_or_mixed_destination(monkeypatch, addresses):
    monkeypatch.setattr(
        worker.socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", (address, 443)) for address in addresses],
    )
    with pytest.raises(worker.UnsafeAddress):
        worker.public_address("example.com", 443)


def test_dns_only_resolves_once_and_prefers_public_ipv4(monkeypatch):
    calls = []

    def resolve(*arguments, **keywords):
        calls.append((arguments, keywords))
        return [
            (0, 0, 0, "", ("2606:4700:4700::1111", 443)),
            (0, 0, 0, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(worker.socket, "getaddrinfo", resolve)
    assert worker.public_address("example.com", 443) == "93.184.216.34"
    assert calls == [(("example.com", 443), {"type": socket.SOCK_STREAM})]


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("proxy", [False, True])
def test_connections_pin_ip_and_keep_original_tls_hostname(monkeypatch, scheme, proxy):
    target = worker._target(f"{scheme}://example.com/product")
    connected, tunneled, tls = [], [], []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        worker.socket, "create_connection", lambda addr, *a: connected.append(addr) or sock
    )
    cls = worker.pinned_connection(target, "93.184.216.34")
    conn = cls("proxy.example", 8080) if proxy else cls(target.host, target.port)
    conn._context = SimpleNamespace(
        wrap_socket=lambda value, server_hostname: tls.append(server_hostname) or value
    )
    if proxy:
        conn.set_tunnel(target.host, target.port)
        conn._tunnel = lambda: tunneled.append((conn._tunnel_host, conn._tunnel_port))
    conn.connect()
    assert connected == [("proxy.example", 8080) if proxy else ("93.184.216.34", target.port)]
    assert tunneled == ([("93.184.216.34", target.port)] if proxy else [])
    assert tls == (["example.com"] if scheme == "https" else [])
    conn.close()


class ProxySocket:
    def __init__(self, status):
        self.response = io.BytesIO(
            f"HTTP/1.0 {status} private-proxy-reason\r\n"
            "Proxy-Authenticate: private-secret\r\n\r\n".encode("ascii")
        )
        self.sent, self.closed = [], False

    def sendall(self, value):
        self.sent.append(value)

    def makefile(self, *arguments, **keywords):
        return self.response

    def close(self):
        self.closed = True


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("status", [202, 401, 403, 407, 429, 500])
def test_real_urllib_proxy_restrictions_stop_once_before_origin_and_tls(
    monkeypatch, scheme, status
):
    target = worker._target(f"{scheme}://example.com/product")
    sock, connected = ProxySocket(status), []
    monkeypatch.setattr(worker, "public_address", lambda *a: "93.184.216.34")
    monkeypatch.setattr(
        worker.socket, "create_connection", lambda addr, *a: connected.append(addr) or sock
    )
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    opener = worker.build_opener(
        worker.PinnedProxyHandler({scheme: "http://user:private-secret@proxy.example:8080"}),
        worker.NoRedirect(),
        worker.PinnedHTTPHandler(target, "93.184.216.34"),
        worker.PinnedHTTPSHandler(target, "93.184.216.34"),
    )
    monkeypatch.setattr(worker, "build_opener", lambda *h: opener)
    result = run_main(monkeypatch, worker_input(url=target.url))
    code = (
        "FETCH_ACCESS_RESTRICTED"
        if status in worker.ACCESS_RESTRICTED_STATUSES
        else ("FETCH_NETWORK_ERROR")
    )
    assert result == {"ok": False, "code": code}
    assert connected == [("proxy.example", 8080)]
    assert len(sock.sent) == 1 and sock.closed
    assert sock.sent[0].startswith(f"CONNECT 93.184.216.34:{target.port} ".encode())
    assert "private" not in json.dumps(result)


def test_plain_http_proxy_uses_tunnel_and_relative_selector(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    handler = worker.PinnedProxyHandler({"http": "http://proxy.example:8080"})
    request = urllib.request.Request("http://example.com/product?q=a")
    handler.proxy_open(request, "http://proxy.example:8080", "http")
    assert request.host == "proxy.example:8080"
    assert request._tunnel_host == "example.com"
    assert request.selector == "/product?q=a"


def test_unsupported_proxy_protocol_is_not_downgraded(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    handler = worker.PinnedProxyHandler({})
    request = urllib.request.Request("https://example.com/product")
    with pytest.raises(worker.UnsafeAddress):
        handler.proxy_open(request, "https://proxy.example:8080", "https")


def test_explicit_proxy_bypass_keeps_the_direct_route(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: True)
    handler = worker.PinnedProxyHandler({})
    request = urllib.request.Request("https://example.com/product")
    assert handler.proxy_open(request, "https://proxy.example:8080", "https") is None
    assert request.host == "example.com" and request._tunnel_host is None


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_successful_proxy_keeps_origin_host_and_isolates_proxy_credentials(monkeypatch, scheme):
    target = worker._target(f"{scheme}://example.com/product?q=a")
    sock = ProxySocket(200)
    sock.streams = iter(
        [
            b"HTTP/1.0 200 Connection Established\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 4\r\n\r\npage",
        ]
    )
    sock.makefile = lambda *a, **kw: io.BytesIO(next(sock.streams))
    monkeypatch.setattr(worker, "public_address", lambda *a: "93.184.216.34")
    monkeypatch.setattr(worker.socket, "create_connection", lambda *a, **kw: sock)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    tls = []
    monkeypatch.setattr(
        worker.ssl,
        "create_default_context",
        lambda: SimpleNamespace(
            verify_mode=worker.ssl.CERT_REQUIRED,
            check_hostname=True,
            wrap_socket=lambda value, server_hostname: tls.append(server_hostname) or value,
        ),
    )
    opener = worker.build_opener(
        worker.PinnedProxyHandler({scheme: "http://user:private-secret@proxy.example:8080"}),
        worker.NoRedirect(),
        worker.PinnedHTTPHandler(target, "93.184.216.34"),
        worker.PinnedHTTPSHandler(target, "93.184.216.34"),
    )
    monkeypatch.setattr(worker, "build_opener", lambda *h: opener)
    result = worker.fetch(target.url, 1, 100)
    assert result["ok"] is True and base64.b64decode(result["body_base64"]) == b"page"
    assert len(sock.sent) == 2
    assert b"Proxy-Authorization: Basic " in sock.sent[0]
    assert sock.sent[1].startswith(b"GET /product?q=a HTTP/1.1\r\n")
    assert b"Host: example.com\r\n" in sock.sent[1]
    assert b"Proxy-Authorization" not in sock.sent[1] and b"private-secret" not in sock.sent[1]
    assert tls == (["example.com"] if scheme == "https" else [])


class Response:
    def __init__(self, status=200, body=b"<html>page</html>", content_type="text/html"):
        self.status, self.body, self.reads = status, body, 0
        self.closed = False
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return None

    def read(self, limit):
        self.reads += 1
        return self.body[:limit]

    def close(self):
        self.closed = True


def mock_response(monkeypatch, response, *, http_error=False):
    calls = []
    monkeypatch.setattr(worker, "public_address", lambda *a: "93.184.216.34")

    def open(request, timeout):
        calls.append((request, timeout))
        if http_error:
            raise HTTPError(
                request.full_url, response.status, "private", response.headers, response
            )
        return response

    monkeypatch.setattr(worker, "build_opener", lambda *h: SimpleNamespace(open=open))
    return calls


@pytest.mark.parametrize("status", [202, 401, 403, 407, 429, 301, 302, 303, 307, 308, 404, 500])
def test_restricted_redirect_and_http_errors_skip_body_and_retry(monkeypatch, status):
    response = Response(status)
    calls = mock_response(monkeypatch, response, http_error=status >= 300)
    output = worker.fetch("https://example.com/product", 1, 100)
    code = (
        "FETCH_REDIRECT_BLOCKED"
        if 300 <= status < 400
        else "FETCH_ACCESS_RESTRICTED"
        if status in worker.ACCESS_RESTRICTED_STATUSES
        else "FETCH_HTTP_ERROR"
    )
    assert output == {"ok": False, "code": code}
    assert len(calls) == 1 and response.reads == 0
    assert worker.NoRedirect().redirect_request(None, None, status, "", {}, "http://127.1") is None


@pytest.mark.parametrize("media_type", ["text/html", "application/xhtml+xml", "text/plain"])
def test_success_envelope_has_original_bytes_and_normalized_metadata(monkeypatch, media_type):
    response = Response(body="产品".encode(), content_type=media_type + "; charset=UTF-8")
    calls = mock_response(monkeypatch, response)
    assert worker.fetch("https://EXAMPLE.com:443/product#part", 1, 100) == {
        "ok": True,
        "url": "https://example.com/product",
        "media_type": media_type,
        "charset": "utf-8",
        "body_base64": base64.b64encode("产品".encode()).decode(),
    }
    assert response.reads == 1 and len(calls) == 1
    request, timeout = calls[0]
    assert timeout == 1 and request.get_method() == "GET"
    assert request.get_header("Accept-encoding") == "identity"
    assert request.get_header("User-agent").startswith("StealHerHeart/")


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Content-Type": "application/json"}, "FETCH_PROTOCOL_ERROR"),
        ({"Content-Type": None}, "FETCH_PROTOCOL_ERROR"),
        ({"Content-Encoding": "gzip"}, "FETCH_PROTOCOL_ERROR"),
        ({"Content-Encoding": "br"}, "FETCH_PROTOCOL_ERROR"),
        ({"Content-Length": "101"}, "FETCH_RESPONSE_TOO_LARGE"),
        ({"Content-Length": "-1"}, "FETCH_RESPONSE_TOO_LARGE"),
        ({"Content-Length": "invalid"}, "FETCH_RESPONSE_TOO_LARGE"),
        ({"Content-Length": "9" * 1000}, "FETCH_RESPONSE_TOO_LARGE"),
        ({"Content-Type": 'text/html; charset="private charset"'}, "FETCH_PROTOCOL_ERROR"),
    ],
)
def test_header_boundaries_stop_before_reading(monkeypatch, headers, code):
    response = Response()
    for name, value in headers.items():
        if name in response.headers:
            del response.headers[name]
        if value is not None:
            response.headers[name] = value
    mock_response(monkeypatch, response)
    assert worker.fetch("https://example.com/", 1, 100) == {"ok": False, "code": code}
    assert response.reads == 0


@pytest.mark.parametrize(
    "name,value",
    [("Content-Length", "10"), ("Content-Type", "text/plain"), ("Content-Encoding", "identity")],
)
def test_duplicate_metadata_headers_are_rejected(monkeypatch, name, value):
    response = Response()
    response.headers[name] = value
    response.headers[name] = value
    mock_response(monkeypatch, response)
    assert worker.fetch("https://example.com/", 1, 100)["code"] == "FETCH_PROTOCOL_ERROR"
    assert response.reads == 0


def test_body_is_bounded_even_without_declared_content_length(monkeypatch):
    response = Response(body=b"x" * 101)
    mock_response(monkeypatch, response)
    assert worker.fetch("https://example.com/", 1, 100) == {
        "ok": False,
        "code": "FETCH_RESPONSE_TOO_LARGE",
    }
    assert response.reads == 1
