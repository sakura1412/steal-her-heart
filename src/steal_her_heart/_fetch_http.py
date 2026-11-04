"""Single public HTTP(S) fetch; bounded and killed/reaped by the parent process.

Standalone stdlib worker: do not import the package, follow redirects, retry,
execute scripts, or emit diagnostics containing raw exceptions or proxy settings.
"""

import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.request
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

MAX_BODY = 1048576
MAX_INPUT = 16384
ACCESS_RESTRICTED_STATUSES = frozenset({202, 401, 403, 407, 429})
MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
_LOCAL_SUFFIXES = (
    "localhost",
    "localdomain",
    "local",
    "internal",
    "home",
    "lan",
    "test",
    "example",
    "invalid",
)
_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


class UnsafeAddress(ValueError):
    """Unsafe destination; never exposes details through the worker protocol."""


class AccessRestricted(RuntimeError):
    """An access challenge, without proxy response, address or credentials."""


class Target(NamedTuple):
    url: str
    scheme: str
    host: str
    port: int


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not getattr(address, "is_site_local", False)
        and (mapped is None or _public_ip(str(mapped)))
        and not (
            address.version == 6 and any(address in network for network in _TRANSITION_NETWORKS)
        )
    )


def _target(value: str) -> Target:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        raise UnsafeAddress()
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username is not None
            or parts.password is not None
            or not parts.hostname
        ):
            raise UnsafeAddress()
        port = 443 if parts.scheme == "https" else 80
        if parts.port is not None and parts.port != port:
            raise UnsafeAddress()
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or "%" in host or "\\" in host:
            raise UnsafeAddress()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # libc accepts legacy numeric IPv4 spellings; do not treat those as
            # domain names or permit reserved/single-label local namespaces.
            labels = host.split(".")
            if (
                len(labels) < 2
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in labels)
                or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", p) for p in labels)
                or any(host == suffix or host.endswith("." + suffix) for suffix in _LOCAL_SUFFIXES)
            ):
                raise UnsafeAddress() from None
        else:
            if not _public_ip(str(address)):
                raise UnsafeAddress()
            host = address.compressed
        if re.search(r"%(?![0-9a-fA-F]{2})", parts.path + parts.query):
            raise UnsafeAddress()
        authority = f"[{host}]" if ":" in host else host
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        normalized = urlunsplit((parts.scheme, authority, path, query, ""))
        if len(normalized) > 2048:
            raise UnsafeAddress()
        return Target(normalized, parts.scheme, host, port)
    except (UnicodeError, ValueError):
        raise UnsafeAddress() from None


def normalize_url(value: str) -> str:
    """Pure URL validation/normalization; DNS is checked again in the worker."""
    return _target(value).url


def public_address(host: str, port: int) -> str:
    """Resolve once; reject mixed answers, then pin a single public address."""
    addresses = {record[4][0] for record in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not _public_ip(value) for value in addresses):
        raise UnsafeAddress()
    return sorted(addresses, key=lambda value: (":" in value, value))[0]


class _TunnelResponse(http.client.HTTPResponse):
    def _read_status(self):
        status = super()._read_status()
        if status[1] in ACCESS_RESTRICTED_STATUSES:
            self.close()
            raise AccessRestricted()
        return status


def pinned_connection(target: Target, address: str):
    base = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection

    class PinnedConnection(base):
        def _tunnel(self):
            response_class = self.response_class
            self.response_class = _TunnelResponse
            try:
                super()._tunnel()
            except AccessRestricted:
                self.close()
                raise
            finally:
                self.response_class = response_class

        def connect(self):
            if self._tunnel_host:
                if self._tunnel_host != target.host or self._tunnel_port != target.port:
                    raise UnsafeAddress()
                self.sock = socket.create_connection(
                    (self.host, self.port), self.timeout, self.source_address
                )
                self._tunnel_host = f"[{address}]" if ":" in address else address
                self._tunnel()
            else:
                if self.host != target.host or self.port != target.port:
                    raise UnsafeAddress()
                self.sock = socket.create_connection(
                    (address, target.port), self.timeout, self.source_address
                )
            if target.scheme == "https":
                self.sock = self._context.wrap_socket(self.sock, server_hostname=target.host)

    return PinnedConnection


class PinnedProxyHandler(ProxyHandler):
    def proxy_open(self, request, proxy, scheme):
        if request.host and urllib.request.proxy_bypass(request.host):
            return None
        # Existing user-configured HTTP proxies are a trusted route. Do not
        # silently downgrade or mis-handle a different proxy protocol.
        proxy_scheme = urlsplit(proxy if "://" in proxy else "http://" + proxy).scheme.lower()
        if proxy_scheme != "http":
            raise UnsafeAddress()
        original_host, original_selector = request.host, request.selector
        result = super().proxy_open(request, proxy, scheme)
        if request.type == "http" and request.host != original_host:
            # Plain HTTP otherwise lets the proxy re-resolve the destination.
            # CONNECT to the pinned public IP, preserving original Host header.
            request._tunnel_host = original_host
            request.selector = original_selector
        return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PinnedHTTPHandler(HTTPHandler):
    def __init__(self, target: Target, address: str):
        super().__init__()
        self.connection = pinned_connection(target, address)

    def http_open(self, request):
        return self.do_open(self.connection, request)


class PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, target: Target, address: str):
        super().__init__(context=ssl.create_default_context())
        self.connection = pinned_connection(target, address)

    def https_open(self, request):
        return self.do_open(self.connection, request, context=self._context)


def fetch(url: str, timeout_s: float, max_bytes: int) -> dict:
    target = _target(url)
    address = public_address(target.host, target.port)
    request = Request(
        target.url,
        headers={
            "User-Agent": "StealHerHeart/0.1 (+https://github.com/sakura1412/steal-her-heart)",
            "Accept": "text/html, application/xhtml+xml, text/plain",
            "Accept-Encoding": "identity",
        },
    )
    opener = build_opener(
        PinnedProxyHandler(),
        NoRedirect(),
        PinnedHTTPHandler(target, address),
        PinnedHTTPSHandler(target, address),
    )
    try:
        response = opener.open(request, timeout=timeout_s)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        if 300 <= status < 400:
            return {"ok": False, "code": "FETCH_REDIRECT_BLOCKED"}
        if status in ACCESS_RESTRICTED_STATUSES:
            return {"ok": False, "code": "FETCH_ACCESS_RESTRICTED"}
        if status != 200:
            return {"ok": False, "code": "FETCH_HTTP_ERROR"}
        media_type = response.headers.get_content_type()
        if len(response.headers.get_all("Content-Type", [])) != 1 or media_type not in MEDIA_TYPES:
            return {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}
        if (
            len(response.headers.get_all("Content-Encoding", [])) > 1
            or response.headers.get("Content-Encoding", "identity").lower() != "identity"
        ):
            return {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}
        lengths = response.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            return {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}
        size = lengths[0].strip() if lengths else None
        if size is not None and (not re.fullmatch(r"[0-9]{1,20}", size) or int(size) > max_bytes):
            return {"ok": False, "code": "FETCH_RESPONSE_TOO_LARGE"}
        charset = response.headers.get_content_charset() or "utf-8"
        if not re.fullmatch(r"[a-z0-9._-]{1,64}", charset):
            return {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            return {"ok": False, "code": "FETCH_RESPONSE_TOO_LARGE"}
        return {
            "ok": True,
            "url": target.url,
            "media_type": media_type,
            "charset": charset,
            "body_base64": base64.b64encode(body).decode("ascii"),
        }


def main() -> None:
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(payload) > MAX_INPUT:
            raise ValueError()
        value = json.loads(payload)
        if not isinstance(value, dict) or set(value) != {"url", "timeout_s", "max_bytes"}:
            raise ValueError()
        url, timeout_s, max_bytes = value["url"], value["timeout_s"], value["max_bytes"]
        if not isinstance(url, str):
            raise ValueError()
        url = normalize_url(url)
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not 0 < timeout_s <= 60
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_BODY
        ):
            raise ValueError()
        result = fetch(url, timeout_s, max_bytes)
    except AccessRestricted:
        result = {"ok": False, "code": "FETCH_ACCESS_RESTRICTED"}
    except UnsafeAddress:
        result = {"ok": False, "code": "FETCH_UNSAFE_ADDRESS"}
    except (socket.timeout, TimeoutError):
        result = {"ok": False, "code": "FETCH_TIMEOUT"}
    except URLError as error:
        code = (
            "FETCH_TIMEOUT"
            if isinstance(error.reason, (socket.timeout, TimeoutError))
            else ("FETCH_NETWORK_ERROR")
        )
        result = {"ok": False, "code": code}
    except (OSError, http.client.HTTPException):
        result = {"ok": False, "code": "FETCH_NETWORK_ERROR"}
    except Exception:
        result = {"ok": False, "code": "FETCH_PROTOCOL_ERROR"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
