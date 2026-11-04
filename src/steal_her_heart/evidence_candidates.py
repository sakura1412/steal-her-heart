"""Bind explicit text spans to immutable sources; candidates remain unvalidated.

Offsets address Unicode code points in the stored normalized text: start is
inclusive, end exclusive, and line numbers begin at one. Nothing here chooses
relevant facts, checks entailment, refreshes sources, or creates Evidence.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .schemas import Contract, Source, Text
from .source_snapshots import (
    MAX_BYTES,
    SnapshotDocument,
    SnapshotError,
    StoredSnapshot,
    _charset,
    _document,
    _Manifest,
    _manifest_bytes,
    _normalize,
)

_Index = Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)]
_Line = Annotated[int, Field(strict=True, ge=1, le=MAX_BYTES)]
_Digest = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=False, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"
    ),
]
_Reference = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=False,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[a-f0-9]{64}$",
    ),
]
_ExactQuote = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=False, min_length=1, max_length=4000)
]
_CandidateId = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=False,
        min_length=74,
        max_length=74,
        pattern=r"^candidate-[a-f0-9]{64}$",
    ),
]
_MESSAGES = {
    "EXTRACT_INVALID_SELECTION": "Candidate targets or text spans are invalid.",
    "EXTRACT_SOURCE_MISMATCH": "Source bytes, normalized text and snapshot metadata do not match.",
    "EXTRACT_INTEGRITY_ERROR": "Candidate does not match the exact source span and identity.",
}


class ExtractionError(RuntimeError):
    """Fixed public messages; never include source text or validation details."""

    def __init__(self, code: str) -> None:
        self.code = (
            code if isinstance(code, str) and code in _MESSAGES else "EXTRACT_INVALID_SELECTION"
        )
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


class TextSpan(Contract):
    start: _Index
    end: _Index

    @model_validator(mode="after")
    def check_span(self) -> "TextSpan":
        if not self.start < self.end or self.end - self.start > 4000:
            raise ValueError("A text span must select between one and 4000 code points")
        return self


class QuoteLocator(TextSpan):
    snapshot_ref: _Reference
    text_sha256: _Digest
    parser_version: Literal["1"] = "1"
    start_line: _Line
    end_line: _Line

    @model_validator(mode="after")
    def check_lines(self) -> "QuoteLocator":
        if self.start_line > self.end_line:
            raise ValueError("Candidate lines must be ordered")
        return self


class EvidenceCandidate(Contract):
    id: _CandidateId
    source: Source
    competitor: Text
    dimension: Text
    fact: _ExactQuote
    quote: _ExactQuote
    locator: QuoteLocator
    method: Literal["verbatim_span"] = "verbatim_span"
    support_status: Literal["unvalidated"] = "unvalidated"

    @model_validator(mode="after")
    def check_verbatim(self) -> "EvidenceCandidate":
        if self.fact != self.quote or not self.quote.strip():
            raise ValueError("A verbatim candidate must retain nonblank exact text")
        if self.source.snapshot_ref != self.locator.snapshot_ref:
            raise ValueError("Candidate source and locator must identify the same snapshot")
        return self


class _Selection(Contract):
    competitor: Text
    dimension: Text
    spans: tuple[TextSpan, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def check_unique(self) -> "_Selection":
        self.competitor.encode("utf-8")
        self.dimension.encode("utf-8")
        if len({(span.start, span.end) for span in self.spans}) != len(self.spans):
            raise ValueError("Repeated text spans are not allowed")
        return self


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _checked_snapshot(snapshot: StoredSnapshot) -> StoredSnapshot:
    """Check the loaded value again; frozen dataclasses are not a trust boundary.

    The caller must first load through SnapshotStore. This pure check reconstructs
    the same manifest and static text; it does not claim a file still exists or
    turn a matching checksum into a finding that its contents are true.
    """
    try:
        if (
            not isinstance(snapshot, StoredSnapshot)
            or type(snapshot.raw) is not bytes
            or type(snapshot.text) is not str
            or len(snapshot.raw) > MAX_BYTES
            or len(snapshot.text.encode("utf-8")) > MAX_BYTES
        ):
            raise ValueError("Invalid snapshot value")
        document = SnapshotDocument.model_validate_json(
            snapshot.document.model_dump_json(warnings=False)
        )
        if document != snapshot.document or _charset(document.charset) != document.charset:
            raise ValueError("Noncanonical snapshot document")
        normalized, title = _normalize(snapshot.raw, document.media_type, document.charset)
        if (
            normalized != snapshot.text
            or title != document.title
            or _sha256(snapshot.raw) != document.source.content_sha256
            or _sha256(snapshot.text.encode("utf-8")) != document.text_sha256
            or len(snapshot.text.splitlines()) != document.line_count
        ):
            raise ValueError("Snapshot content differs from its metadata")
        manifest = _Manifest(
            url=document.source.url,
            source_name=document.source.source_name,
            fetched_at=document.source.fetched_at,
            title=document.title,
            media_type=document.media_type,
            charset=document.charset,
            content_sha256=document.source.content_sha256,
            text_sha256=document.text_sha256,
            line_count=document.line_count,
        )
        digest = _sha256(_manifest_bytes(manifest))
        if _document(manifest, digest) != document:
            raise ValueError("Snapshot identity differs from the canonical manifest")
        return StoredSnapshot(document=document, raw=snapshot.raw, text=snapshot.text)
    except (AttributeError, ValueError, TypeError, UnicodeError, SnapshotError):
        raise ExtractionError("EXTRACT_SOURCE_MISMATCH") from None


def _candidate(
    snapshot: StoredSnapshot, competitor: str, dimension: str, span: TextSpan
) -> EvidenceCandidate:
    text, document = snapshot.text, snapshot.document
    if span.end > len(text):
        raise ExtractionError("EXTRACT_INVALID_SELECTION")
    quote = text[span.start : span.end]
    if not quote.strip():
        raise ExtractionError("EXTRACT_INVALID_SELECTION")
    locator = QuoteLocator(
        snapshot_ref=document.source.snapshot_ref,
        text_sha256=document.text_sha256,
        parser_version=document.parser_version,
        start=span.start,
        end=span.end,
        start_line=text.count("\n", 0, span.start) + 1,
        end_line=text.count("\n", 0, span.end - 1) + 1,
    )
    identity = json.dumps(
        {
            "schema_version": 1,
            "method": "verbatim_span",
            "competitor": competitor,
            "dimension": dimension,
            "locator": locator.model_dump(mode="json"),
            "quote": quote,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return EvidenceCandidate(
        id="candidate-" + _sha256(identity),
        source=document.source,
        competitor=competitor,
        dimension=dimension,
        fact=quote,
        quote=quote,
        locator=locator,
    )


def extract_candidates(
    snapshot: StoredSnapshot,
    *,
    competitor: str,
    dimension: str,
    spans: tuple[TextSpan, ...],
) -> tuple[EvidenceCandidate, ...]:
    """Bind all explicit selections atomically, preserving input order and text.

    The target labels are supplied by the caller. No claim is made that the
    selected text discusses that entity or dimension; a later Validator must
    inspect that semantic relationship.
    """
    try:
        if not isinstance(spans, (tuple, list)) or not 1 <= len(spans) <= 20:
            raise ValueError("A bounded sequence of explicit spans is required")
        # Rebuild rather than trust model_copy/model_construct instances that
        # can bypass the normal Pydantic boundary.
        selection = _Selection.model_validate(
            {
                "competitor": competitor,
                "dimension": dimension,
                "spans": [
                    span.model_dump() if isinstance(span, TextSpan) else span for span in spans
                ],
            }
        )
    except (ValueError, TypeError):
        raise ExtractionError("EXTRACT_INVALID_SELECTION") from None
    checked = _checked_snapshot(snapshot)
    return tuple(
        _candidate(checked, selection.competitor, selection.dimension, span)
        for span in selection.spans
    )


def verify_candidate(candidate: EvidenceCandidate, snapshot: StoredSnapshot) -> None:
    """Verify exact binding and identity only; this is not semantic validation."""
    checked = _checked_snapshot(snapshot)
    try:
        parsed = EvidenceCandidate.model_validate_json(candidate.model_dump_json(warnings=False))
        if parsed != candidate or parsed.source != checked.document.source:
            raise ValueError("Candidate does not match the loaded source")
        expected = _candidate(
            checked,
            parsed.competitor,
            parsed.dimension,
            TextSpan(start=parsed.locator.start, end=parsed.locator.end),
        )
        if parsed != expected:
            raise ValueError("Candidate is not the canonical exact text binding")
    except (AttributeError, ValueError, TypeError, ValidationError, ExtractionError):
        raise ExtractionError("EXTRACT_INTEGRITY_ERROR") from None
