"""Real, no-key search through Registry; hits are discovery leads, not Evidence."""

import asyncio
import base64
import binascii
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Optional, Protocol
from uuid import UUID, uuid4

from pydantic import Field, HttpUrl, field_validator, model_validator

from .schemas import Contract, Text, ToolContext, utc_now
from .search_html import SearchHit, SearchPageError, parse_duckduckgo_html
from .search_ledger import SearchLedger, SearchLedgerError
from .tools import Tool, ToolExecutionError

ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_BYTES = 1048576
MESSAGES = {
    "SEARCH_ACCESS_RESTRICTED": (
        "Public search requires verification or is rate limited; stopped without retry."
    ),
    "SEARCH_REDIRECT_BLOCKED": "Search returned a redirect; it was not followed.",
    "SEARCH_HTTP_ERROR": "The public search service returned an unsuccessful HTTP status.",
    "SEARCH_PROTOCOL_ERROR": (
        "The search transport did not return the expected bounded HTML response."
    ),
    "SEARCH_NETWORK_ERROR": "The public search service could not be reached; no automatic retry.",
    "SEARCH_UNSAFE_ADDRESS": "The search endpoint did not resolve to allowed public addresses.",
    "SEARCH_RESPONSE_TOO_LARGE": "Search response exceeded the configured size limit.",
    "SEARCH_TIMEOUT": "Search exceeded its total deadline; the network worker was stopped.",
    "SEARCH_CANCELLED": "Search was cancelled; its reserved attempt remains charged.",
    "SEARCH_FORMAT_ERROR": (
        "The search page format was not recognized; no empty result was inferred."
    ),
    "SEARCH_NO_USABLE_RESULTS": "Search links were present but none met the URL requirements.",
}


class SearchRequest(Contract):
    query: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    max_results: Annotated[int, Field(strict=True, ge=1, le=10)] = 5
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("query", mode="before")
    @classmethod
    def one_query(cls, value):
        if isinstance(value, str) and any(
            ord(character) < 32 or 127 <= ord(character) < 160 for character in value
        ):
            raise ValueError("A search query must be one line without control characters")
        return value


class SearchPage(Contract):
    provider: Text = "duckduckgo_html"
    search_url: HttpUrl
    fetched_at: datetime
    response_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    results: tuple[SearchHit, ...] = Field(max_length=10)

    @model_validator(mode="after")
    def check_page(self) -> "SearchPage":
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("Search time must include a timezone")
        if len({str(hit.url) for hit in self.results}) != len(self.results):
            raise ValueError("Search result URLs must be unique")
        if [hit.rank for hit in self.results] != list(range(1, len(self.results) + 1)):
            raise ValueError("Search hits must have consecutive ranks")
        return self


class SearchResponse(SearchPage):
    task_id: UUID
    request_id: UUID
    query: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    evidence_eligible: Literal[False] = False
    limitations: tuple[Text, ...] = (
        "Only the first search page is inspected; result pages have not been fetched or verified.",
        "Titles and snippets are discovery leads, "
        "not verified facts or completed collection fields.",
    )


class SearchFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if code in MESSAGES else "SEARCH_PROTOCOL_ERROR"
        self.message = MESSAGES[self.code]
        super().__init__(self.message)


class SearchProvider(Protocol):
    name: str

    async def search(self, request: SearchRequest, *, timeout_s: float) -> SearchPage: ...


async def _http_worker(query: str, *, timeout_s: float, max_bytes: int = MAX_BYTES) -> bytes:
    """One fixed child, bounded input/output, no shell, no background HTTP threads."""
    started = time.monotonic()
    worker = Path(__file__).with_name("_search_http.py")
    process = None
    output_limit = 2 * max_bytes + 8192

    async def exchange(payload: bytes) -> bytes:
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        chunks, received = [], 0
        while True:
            chunk = await process.stdout.read(min(65536, output_limit + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > output_limit:
                raise SearchFailure("SEARCH_PROTOCOL_ERROR")
            chunks.append(chunk)
        await process.wait()
        return b"".join(chunks)

    try:
        # asyncio closes and reaps its transport if creation is cancelled.
        # Do not shield creation: it must share the request's deadline.
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable,
                str(worker),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=65536,
            ),
            timeout=timeout_s,
        )
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise SearchFailure("SEARCH_TIMEOUT")
        payload = json.dumps(
            {"query": query, "timeout_s": remaining, "max_bytes": max_bytes},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(payload) > 4096:
            raise SearchFailure("SEARCH_PROTOCOL_ERROR")
        output = await asyncio.wait_for(exchange(payload), timeout=remaining)
        if process.returncode != 0:
            raise SearchFailure("SEARCH_PROTOCOL_ERROR")
        try:
            data = json.loads(output)
            if not isinstance(data, dict) or type(data.get("ok")) is not bool:
                raise ValueError("Unexpected transport envelope")
            if not data["ok"]:
                raise SearchFailure(data.get("code", "SEARCH_PROTOCOL_ERROR"))
            if set(data) != {"ok", "body_base64"}:
                raise ValueError("Unexpected transport fields")
            body = base64.b64decode(data["body_base64"], validate=True)
        except (ValueError, TypeError, KeyError, binascii.Error):
            raise SearchFailure("SEARCH_PROTOCOL_ERROR") from None
        if len(body) > max_bytes:
            raise SearchFailure("SEARCH_RESPONSE_TOO_LARGE")
        return body
    except asyncio.TimeoutError:
        raise SearchFailure("SEARCH_TIMEOUT") from None
    finally:
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process.stdin.close()
            # Drain after killing, discarding bounded chunks rather than
            # retaining output; full pipes must not prevent transport cleanup.
            while await process.stdout.read(65536):
                pass
            await process.wait()


class DuckDuckGoSearchProvider:
    name = "duckduckgo_html"

    async def search(self, request: SearchRequest, *, timeout_s: float) -> SearchPage:
        from urllib.parse import urlencode

        body = await _http_worker(request.query, timeout_s=timeout_s)
        try:
            results = parse_duckduckgo_html(body, request.max_results)
        except SearchPageError as error:
            raise SearchFailure(error.code) from None
        return SearchPage(
            search_url=ENDPOINT + "?" + urlencode({"q": request.query}),
            fetched_at=utc_now(),
            response_sha256=hashlib.sha256(body).hexdigest(),
            results=results,
        )


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search public web once without a key; stop on restrictions; hits are leads only"
    input_model = SearchRequest
    output_model = SearchResponse
    required_capabilities = frozenset({"web_search"})

    def __init__(
        self,
        *,
        ledger: SearchLedger,
        provider: Optional[SearchProvider] = None,
        max_calls: int = 30,
    ) -> None:
        self.ledger = ledger
        self.provider = provider if provider is not None else DuckDuckGoSearchProvider()
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("Search limit must be a positive integer")
        self.max_calls = max_calls

    async def run(self, arguments: SearchRequest, context: ToolContext) -> SearchResponse:
        started = time.monotonic()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "query": arguments.query,
                    "max_results": arguments.max_results,
                    "provider": self.provider.name,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        try:
            reservation = self.ledger.reserve(
                task_id=context.task_id,
                request_id=arguments.request_id,
                fingerprint=fingerprint,
                max_calls=self.max_calls,
            )
        except SearchLedgerError as error:
            raise ToolExecutionError(error.code, error.message) from None
        if reservation.status == "succeeded":
            try:
                cached = SearchResponse.model_validate_json(reservation.response_json)
                if (
                    cached.task_id != context.task_id
                    or cached.request_id != arguments.request_id
                    or cached.query != arguments.query
                    or cached.provider != self.provider.name
                    or len(cached.results) > arguments.max_results
                ):
                    raise ValueError("Cached response does not match its request")
                return cached
            except (ValueError, TypeError):
                raise ToolExecutionError(
                    "SEARCH_CACHE_INVALID", "Stored search result is invalid."
                ) from None
        if reservation.status == "failed":
            raise ToolExecutionError(reservation.error_code, reservation.error_message)
        try:
            remaining = context.timeout_s - (time.monotonic() - started) - 0.05
            if remaining <= 0:
                raise SearchFailure("SEARCH_TIMEOUT")
            page = await self.provider.search(arguments, timeout_s=remaining)
            page = SearchPage.model_validate(page.model_dump())
            if page.provider != self.provider.name or len(page.results) > arguments.max_results:
                raise SearchFailure("SEARCH_PROTOCOL_ERROR")
            response = SearchResponse(
                **page.model_dump(),
                task_id=context.task_id,
                request_id=arguments.request_id,
                query=arguments.query,
            )
        except asyncio.CancelledError:
            self.ledger.finish_error(
                arguments.request_id, "SEARCH_CANCELLED", MESSAGES["SEARCH_CANCELLED"]
            )
            raise
        except SearchFailure as error:
            self.ledger.finish_error(arguments.request_id, error.code, error.message)
            raise ToolExecutionError(error.code, error.message) from None
        except Exception:
            code = "SEARCH_PROTOCOL_ERROR"
            self.ledger.finish_error(arguments.request_id, code, MESSAGES[code])
            raise ToolExecutionError(code, MESSAGES[code]) from None
        self.ledger.finish_success(arguments.request_id, response.model_dump_json())
        return response
