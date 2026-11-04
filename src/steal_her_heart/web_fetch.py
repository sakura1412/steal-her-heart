"""Bounded public-page acquisition and durable source snapshots, not fact validation."""

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

from pydantic import Field, HttpUrl, StringConstraints, field_validator, model_validator

from ._fetch_http import normalize_url
from .schemas import Contract, Text, ToolContext, utc_now
from .search_ledger import SearchLedger, SearchLedgerError
from .source_snapshots import SnapshotDocument, SnapshotError, SnapshotStore
from .tools import Tool, ToolExecutionError

MAX_BYTES = 1048576
PREVIEW_CHARS = 12000
MESSAGES = {
    "FETCH_ACCESS_RESTRICTED": "The page requires verification or access; stopped without retry.",
    "FETCH_REDIRECT_BLOCKED": "The page returned a redirect; it was not followed.",
    "FETCH_HTTP_ERROR": "The page returned an unsuccessful HTTP status.",
    "FETCH_PROTOCOL_ERROR": "The fetch worker did not return the expected bounded text response.",
    "FETCH_NETWORK_ERROR": "The page could not be reached; no automatic retry.",
    "FETCH_UNSAFE_ADDRESS": "The page address does not meet public HTTP(S) requirements.",
    "FETCH_RESPONSE_TOO_LARGE": "The page exceeded the configured size limit.",
    "FETCH_TIMEOUT": "Page acquisition exceeded its deadline; the network worker was stopped.",
    "FETCH_CANCELLED": "Page acquisition was cancelled; its reserved attempt remains charged.",
}


def _public_url(url: HttpUrl) -> HttpUrl:
    # Use the worker's pure normalization before reserving an attempt, and
    # independently recheck DNS in the worker immediately before connection.
    return HttpUrl(normalize_url(str(url)))


class FetchRequest(Contract):
    url: HttpUrl
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("url", mode="before")
    @classmethod
    def check_url_text(cls, value):
        raw = str(value) if isinstance(value, HttpUrl) else value
        if (
            not isinstance(raw, str)
            or len(raw) > 2048
            or any(ord(char) < 33 or 127 <= ord(char) < 160 for char in raw)
        ):
            raise ValueError("A page URL must be bounded and contain no controls or whitespace")
        return value

    @field_validator("url")
    @classmethod
    def check_public_url(cls, value: HttpUrl) -> HttpUrl:
        if len(str(value)) > 2048:
            raise ValueError("The normalized URL exceeds the supported length")
        return _public_url(value)


class FetchedPage(Contract):
    url: HttpUrl
    media_type: Literal["text/html", "application/xhtml+xml", "text/plain"]
    charset: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    body: Annotated[bytes, Field(strict=True, max_length=MAX_BYTES)]
    fetched_at: datetime

    @model_validator(mode="after")
    def check_time(self) -> "FetchedPage":
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("Acquisition time must include a timezone")
        _public_url(self.url)
        return self


class FetchResponse(Contract):
    task_id: UUID
    request_id: UUID
    document: SnapshotDocument
    text_preview: Annotated[
        str,
        StringConstraints(
            strict=True, strip_whitespace=False, min_length=1, max_length=PREVIEW_CHARS
        ),
    ]
    text_truncated: bool
    facts_validated: Literal[False] = False
    limitations: tuple[Text, ...] = (
        "Page content is untrusted source material; no factual claim has been validated.",
        "Scripts are not run; only supported static HTML or plain text is acquired.",
        "Publication date is unknown; acquisition time is not a publication date.",
        "Redirects are not followed in this acquisition module.",
    )


class FetchFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and code in MESSAGES else "FETCH_PROTOCOL_ERROR"
        self.message = MESSAGES[self.code]
        super().__init__(self.message)


class FetchProvider(Protocol):
    name: str

    async def fetch(self, request: FetchRequest, *, timeout_s: float) -> FetchedPage: ...


async def _fetch_worker(url: str, *, timeout_s: float) -> dict:
    """One trusted worker; cap pipe output while reading and reap on every exit."""
    started, process = time.monotonic(), None
    output_limit = 2 * MAX_BYTES + 8192

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
                raise FetchFailure("FETCH_PROTOCOL_ERROR")
            chunks.append(chunk)
        await process.wait()
        return b"".join(chunks)

    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("_fetch_http.py")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=65536,
            ),
            timeout=timeout_s,
        )
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise FetchFailure("FETCH_TIMEOUT")
        payload = json.dumps(
            {"url": url, "timeout_s": remaining, "max_bytes": MAX_BYTES}, ensure_ascii=False
        ).encode("utf-8")
        if len(payload) > 12288:
            raise FetchFailure("FETCH_PROTOCOL_ERROR")
        raw = await asyncio.wait_for(exchange(payload), timeout=remaining)
        if process.returncode != 0:
            raise FetchFailure("FETCH_PROTOCOL_ERROR")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get("ok")) is not bool:
                raise ValueError("Invalid transport envelope")
            if not data["ok"]:
                raise FetchFailure(data.get("code"))
            if set(data) != {"ok", "url", "media_type", "charset", "body_base64"}:
                raise ValueError("Unexpected transport fields")
            body = base64.b64decode(data.pop("body_base64"), validate=True)
            if len(body) > MAX_BYTES:
                raise FetchFailure("FETCH_RESPONSE_TOO_LARGE")
            return {
                "url": data["url"],
                "media_type": data["media_type"],
                "charset": data["charset"],
                "body": body,
                "fetched_at": utc_now(),
            }
        except (ValueError, TypeError, KeyError, binascii.Error):
            raise FetchFailure("FETCH_PROTOCOL_ERROR") from None
    except asyncio.TimeoutError:
        raise FetchFailure("FETCH_TIMEOUT") from None
    finally:
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process.stdin.close()
            while await process.stdout.read(65536):
                pass
            await process.wait()


class PublicFetchProvider:
    name = "public_http"

    async def fetch(self, request: FetchRequest, *, timeout_s: float) -> FetchedPage:
        try:
            return FetchedPage.model_validate(
                await _fetch_worker(str(request.url), timeout_s=timeout_s)
            )
        except ValueError:
            raise FetchFailure("FETCH_PROTOCOL_ERROR") from None


class FetchLedger(SearchLedger):
    """Same durable intent primitive, with a distinct database identity for fetch."""

    _application_id = 0x53484846


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Acquire one public static page and save a verified local snapshot, not facts"
    input_model = FetchRequest
    output_model = FetchResponse
    required_capabilities = frozenset({"web_fetch"})

    def __init__(
        self,
        *,
        ledger: FetchLedger,
        snapshots: SnapshotStore,
        provider: Optional[FetchProvider] = None,
        max_calls: int = 60,
    ) -> None:
        # The database identity prevents accidentally mixing search/fetch quotas.
        if not isinstance(ledger, FetchLedger):
            raise ValueError("Page acquisition requires a dedicated fetch ledger")
        self.ledger, self.snapshots = ledger, snapshots
        self.provider = provider if provider is not None else PublicFetchProvider()
        if type(max_calls) is not int or not 1 <= max_calls <= 2**63 - 1:
            raise ValueError("Fetch limit must be a positive bounded integer")
        self.max_calls = max_calls

    @staticmethod
    def _ledger_error(error: SearchLedgerError) -> ToolExecutionError:
        return ToolExecutionError(
            error.code.replace("SEARCH_", "FETCH_", 1),
            "Page acquisition ledger rejected the request or could not be accessed safely.",
        )

    async def run(self, arguments: FetchRequest, context: ToolContext) -> FetchResponse:
        started = time.monotonic()
        try:
            self.snapshots.check_support()
        except SnapshotError as error:
            raise ToolExecutionError(error.code, error.message) from None
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "tool": self.name,
                    "url": str(arguments.url),
                    "provider": self.provider.name,
                    "snapshot_format": "1",
                },
                sort_keys=True,
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
            raise self._ledger_error(error) from None
        if reservation.status == "succeeded":
            try:
                cached = FetchResponse.model_validate_json(reservation.response_json)
                snapshot = self.snapshots.load(cached.document.source.snapshot_ref)
                if (
                    cached.task_id != context.task_id
                    or cached.request_id != arguments.request_id
                    or cached.document.source.url != arguments.url
                    or snapshot.document != cached.document
                    or cached.text_preview != snapshot.text[:PREVIEW_CHARS]
                    or cached.text_truncated != (len(snapshot.text) > PREVIEW_CHARS)
                ):
                    raise ValueError("Stored receipt does not match its request and snapshot")
                return cached
            except (ValueError, TypeError, SnapshotError):
                raise ToolExecutionError(
                    "FETCH_CACHE_INVALID", "Stored source is invalid."
                ) from None
        if reservation.status == "failed":
            raise ToolExecutionError(reservation.error_code, reservation.error_message)
        try:
            remaining = context.timeout_s - (time.monotonic() - started) - 0.05
            if remaining <= 0:
                raise FetchFailure("FETCH_TIMEOUT")
            page = await self.provider.fetch(arguments, timeout_s=remaining)
            page = FetchedPage.model_validate(page.model_dump())
            if page.url != arguments.url:
                raise FetchFailure("FETCH_PROTOCOL_ERROR")
            document = self.snapshots.save(
                url=str(page.url),
                media_type=page.media_type,
                charset=page.charset,
                body=page.body,
                fetched_at=page.fetched_at,
            )
            snapshot = self.snapshots.load(document.source.snapshot_ref)
            response = FetchResponse(
                task_id=context.task_id,
                request_id=arguments.request_id,
                document=document,
                text_preview=snapshot.text[:PREVIEW_CHARS],
                text_truncated=len(snapshot.text) > PREVIEW_CHARS,
            )
        except asyncio.CancelledError:
            try:
                self.ledger.finish_error(
                    arguments.request_id, "FETCH_CANCELLED", MESSAGES["FETCH_CANCELLED"]
                )
            except SearchLedgerError:
                # The pre-reserved intent remains unresolved and cannot resend.
                # A storage fault must not replace the cancellation signal.
                pass
            raise
        except (FetchFailure, SnapshotError) as error:
            try:
                self.ledger.finish_error(arguments.request_id, error.code, error.message)
            except SearchLedgerError as ledger_error:
                raise self._ledger_error(ledger_error) from None
            raise ToolExecutionError(error.code, error.message) from None
        except Exception:
            try:
                self.ledger.finish_error(
                    arguments.request_id, "FETCH_PROTOCOL_ERROR", MESSAGES["FETCH_PROTOCOL_ERROR"]
                )
            except SearchLedgerError as error:
                raise self._ledger_error(error) from None
            raise ToolExecutionError(
                "FETCH_PROTOCOL_ERROR", MESSAGES["FETCH_PROTOCOL_ERROR"]
            ) from None
        try:
            self.ledger.finish_success(arguments.request_id, response.model_dump_json())
        except SearchLedgerError as error:
            raise self._ledger_error(error) from None
        return response
