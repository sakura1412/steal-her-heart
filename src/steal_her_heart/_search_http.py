"""Bounded, killable HTTP worker for one fixed public search endpoint.

This is a network subprocess, not an AI subagent. Its parent enforces a total
deadline and reaps it on cancellation. Never print exceptions or proxy settings.
"""

import base64
import http.client
import ipaddress
import json
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

HOST = "html.duckduckgo.com"
ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_BODY = 1048576
ACCESS_RESTRICTED_STATUSES = frozenset({202, 401, 403, 407, 429})


class AccessRestricted(RuntimeError):
    """An access challenge; carries no proxy response, address, or credentials."""


class _TunnelResponse(http.client.HTTPResponse):
    def _read_status(self):
        status = super()._read_status()
        if status[1] in ACCESS_RESTRICTED_STATUSES:
            self.close()
            raise AccessRestricted()
        return status


class UnsafeAddress(ValueError):
    pass


def public_address(host: str) -> str:
    """Resolve once and pin the connection, refusing mixed public/private DNS."""
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise UnsafeAddress("No usable address")
    for value in addresses:
        address = ipaddress.ip_address(value)
        mapped = getattr(address, "ipv4_mapped", None)
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise UnsafeAddress("Search endpoint did not resolve to public addresses")
    return sorted(addresses, key=lambda value: (":" in value, value))[0]


def pinned_connection(address: str):
    class SearchHTTPSConnection(http.client.HTTPSConnection):
        def _tunnel(self):
            # The stdlib otherwise turns every rejected CONNECT into OSError,
            # losing the distinction between a network failure and a challenge.
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
            # urllib may use a proxy explicitly configured by the user. That is
            # a trusted route; the CONNECT target remains the pinned public IP.
            if self._tunnel_host:
                if self._tunnel_host != HOST or self._tunnel_port != 443:
                    raise UnsafeAddress("Unexpected tunnel destination")
                self.sock = socket.create_connection(
                    (self.host, self.port), self.timeout, self.source_address
                )
                self._tunnel_host = f"[{address}]" if ":" in address else address
                self._tunnel()
            else:
                if self.host != HOST or self.port != 443:
                    raise UnsafeAddress("Unexpected search destination")
                self.sock = socket.create_connection(
                    (address, 443), self.timeout, self.source_address
                )
            # Certificate verification and SNI use the provider hostname, not
            # the numeric address or the user's proxy hostname.
            self.sock = self._context.wrap_socket(self.sock, server_hostname=HOST)

    return SearchHTTPSConnection


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, address: str):
        super().__init__(context=ssl.create_default_context())
        self.connection = pinned_connection(address)

    def https_open(self, request):
        return self.do_open(self.connection, request, context=self._context)


def fetch(query: str, timeout_s: float, max_bytes: int) -> dict:
    address = public_address(HOST)
    request = Request(
        ENDPOINT + "?" + urlencode({"q": query}),
        headers={
            "User-Agent": "StealHerHeart/0.1 (+https://github.com/sakura1412/steal-her-heart)",
            "Accept": "text/html",
            "Accept-Encoding": "identity",
        },
    )
    opener = build_opener(NoRedirect(), PinnedHTTPSHandler(address))
    try:
        response = opener.open(request, timeout=timeout_s)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        # Do not follow even same-provider redirects. Never automatically retry
        # access challenges, authentication, rate limits, or other HTTP errors.
        if 300 <= status < 400:
            return {"ok": False, "code": "SEARCH_REDIRECT_BLOCKED"}
        if status in ACCESS_RESTRICTED_STATUSES:
            return {"ok": False, "code": "SEARCH_ACCESS_RESTRICTED"}
        if status != 200:
            return {"ok": False, "code": "SEARCH_HTTP_ERROR"}
        if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            return {"ok": False, "code": "SEARCH_PROTOCOL_ERROR"}
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            return {"ok": False, "code": "SEARCH_PROTOCOL_ERROR"}
        size = response.headers.get("Content-Length")
        if size and (not size.isdigit() or int(size) > max_bytes):
            return {"ok": False, "code": "SEARCH_RESPONSE_TOO_LARGE"}
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            return {"ok": False, "code": "SEARCH_RESPONSE_TOO_LARGE"}
        return {"ok": True, "body_base64": base64.b64encode(body).decode("ascii")}


def main() -> None:
    try:
        value = json.loads(sys.stdin.buffer.read(4097))
        if not isinstance(value, dict) or set(value) != {"query", "timeout_s", "max_bytes"}:
            raise ValueError("Invalid worker parameters")
        query, timeout_s, max_bytes = value["query"], value["timeout_s"], value["max_bytes"]
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 512
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in query)
        ):
            raise ValueError("Invalid query")
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not 0 < timeout_s <= 60
        ):
            raise ValueError("Invalid timeout")
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_BODY
        ):
            raise ValueError("Invalid response limit")
        result = fetch(query, timeout_s, max_bytes)
    except AccessRestricted:
        result = {"ok": False, "code": "SEARCH_ACCESS_RESTRICTED"}
    except UnsafeAddress:
        result = {"ok": False, "code": "SEARCH_UNSAFE_ADDRESS"}
    except (socket.timeout, TimeoutError):
        result = {"ok": False, "code": "SEARCH_TIMEOUT"}
    except (URLError, OSError, http.client.HTTPException):
        result = {"ok": False, "code": "SEARCH_NETWORK_ERROR"}
    except Exception:
        result = {"ok": False, "code": "SEARCH_PROTOCOL_ERROR"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
