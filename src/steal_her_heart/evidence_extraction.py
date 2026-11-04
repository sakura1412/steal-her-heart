"""Read-only binding of explicit text spans; no model, fact validation or asset reuse."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from .evidence_candidates import EvidenceCandidate, ExtractionError, TextSpan, extract_candidates
from .schemas import Contract, Text, ToolContext
from .source_snapshots import SnapshotError, SnapshotStore
from .tools import Tool, ToolExecutionError

SnapshotReference = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=False,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[a-f0-9]{64}$",
    ),
]
TextDigest = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=False, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"
    ),
]


class ExtractionRequest(Contract):
    snapshot_ref: SnapshotReference
    text_sha256: TextDigest
    competitor: Text
    dimension: Text
    spans: tuple[TextSpan, ...] = Field(min_length=1, max_length=20)


class ExtractionResponse(Contract):
    task_id: UUID
    status: Literal["candidates_only"] = "candidates_only"
    candidates: tuple[EvidenceCandidate, ...] = Field(min_length=1, max_length=20)
    facts_validated: Literal[False] = False
    limitations: tuple[Text, ...] = (
        "The caller selects spans explicitly; there is no automatic semantic extraction.",
        "Candidate facts are verbatim quotes; "
        "target relevance and semantic support are unvalidated.",
        "Source acquisition time is retained; no freshness or cross-task reuse decision is made.",
        "Candidates are not verified Evidence, a Validator PASS, an analysis or a report.",
    )


class EvidenceExtractionTool(Tool):
    name = "extract_features"
    description = "Bind explicitly selected source spans to unvalidated, traceable candidates"
    input_model = ExtractionRequest
    output_model = ExtractionResponse
    required_capabilities = frozenset({"evidence_extraction"})

    def __init__(self, *, snapshots: SnapshotStore) -> None:
        self.snapshots = snapshots

    async def run(self, arguments: ExtractionRequest, context: ToolContext) -> ExtractionResponse:
        try:
            snapshot = self.snapshots.load(arguments.snapshot_ref)
            if snapshot.document.text_sha256 != arguments.text_sha256:
                raise ToolExecutionError(
                    "EXTRACT_SOURCE_MISMATCH", "Selected text version does not match the snapshot."
                )
            candidates = extract_candidates(
                snapshot,
                competitor=arguments.competitor,
                dimension=arguments.dimension,
                spans=arguments.spans,
            )
            return ExtractionResponse(task_id=context.task_id, candidates=candidates)
        except SnapshotError as error:
            raise ToolExecutionError(
                error.code.replace("FETCH_", "EXTRACT_", 1),
                "The local source snapshot is unavailable or failed its integrity checks.",
            ) from None
        except ExtractionError as error:
            raise ToolExecutionError(error.code, error.message) from None
