"""TEST ONLY reference coordinator with explicit scripted missing-module doubles.

Real: Registry, rule brief recognition, search parser/provider, fetch provider,
durable call ledgers, snapshot store, exact span binding, collection/analysis
schemas, state guards and MemoryStore. Replaced: network I/O, Planner, semantic
extraction/applicability, Validator, Analysis and Reporter. No model runs.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from steal_her_heart import web_fetch, web_search
from steal_her_heart.brief_recognition import BriefRecognition, BriefRecognitionTool
from steal_her_heart.collection_template import (
    CollectedField,
    CollectedInformation,
    CollectionSearchAttempt,
    CompetitorCollection,
    build_collection_template,
)
from steal_her_heart.evidence_candidates import EvidenceCandidate, verify_candidate
from steal_her_heart.evidence_extraction import EvidenceExtractionTool
from steal_her_heart.memory import MemoryStore
from steal_her_heart.schemas import (
    Analysis,
    AnalysisClaim,
    Evidence,
    EvidenceBundle,
    PendingConfirmation,
    ReportDraft,
    ResearchMemory,
    ResearchPlan,
    ResearchState,
    ResearchWorkItem,
    StateContext,
    TaskSnapshot,
    ToolContext,
    ValidationResult,
)
from steal_her_heart.search_ledger import SearchLedger
from steal_her_heart.source_snapshots import SnapshotStore
from steal_her_heart.state_machine import transition
from steal_her_heart.tools import ToolRegistry
from steal_her_heart.web_fetch import FetchLedger, WebFetchTool
from steal_her_heart.web_search import WebSearchTool

FIXED_TIME = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).with_name("fixtures") / "workflow_cases.json"
SIMULATION_HEADER = "# SIMULATION ONLY — 合成场景，非真实竞品报告"


def load_case(name):
    return json.loads(FIXTURES.read_text())["cases"][name]


class SimulationStop(Exception):
    pass


class FixtureNetwork:
    """Supply synthetic response bytes exactly at the network boundaries."""

    def __init__(self, case, fault=None):
        self.case, self.fault = case, fault
        self.calls = {"search": 0, "fetch": 0}
        self.pages = {page["url"]: page for row in case["observations"] for page in row["pages"]}

    async def search_bytes(self, query, *, timeout_s):
        self.calls["search"] += 1
        if self.fault == "search_restricted":
            raise web_search.SearchFailure("SEARCH_ACCESS_RESTRICTED")
        if self.fault == "unknown_search_layout":
            return b"<html>Unknown synthetic layout</html>"
        row = next(
            r for r in self.case["observations"] if query == r["competitor"] + " " + r["dimension"]
        )
        if not row["pages"]:
            return b'<div class="no-results">No results found.</div>'
        return "".join(
            '<div class="result"><a class="result__a" href="' + escape(page["url"]) + '">'
            'Synthetic source</a><a class="result__snippet">'
            "SYNTHETIC SNIPPET LIE: USD 999</a></div>"
            for page in row["pages"]
        ).encode()

    async def fetch_page(self, url, *, timeout_s):
        self.calls["fetch"] += 1
        if self.fault == "fetch_restricted":
            raise web_fetch.FetchFailure("FETCH_ACCESS_RESTRICTED")
        if self.fault == "http_404":
            raise web_fetch.FetchFailure("FETCH_HTTP_ERROR")
        quote = self.pages[url]["quote"]
        body = (
            "<html><head><title>Synthetic fixture</title></head><body><p>"
            + escape(quote)
            + "</p><script>UNTRUSTED SCRIPT TEXT</script></body></html>"
        ).encode()
        return {
            "url": url,
            "media_type": "text/html",
            "charset": "utf-8",
            "body": body,
            "fetched_at": FIXED_TIME,
        }


class FixturePlanner:
    """Deterministic scenario adapter, not the missing production Planner."""

    def plan(self, brief, case):
        expected = {(r["competitor"], r["dimension"]) for r in case["observations"]}
        actual = {(c, d) for c in brief.competitors for d in brief.dimensions}
        assert actual == expected, (
            "The real brief recognizer no longer matches this explicit fixture"
        )
        tasks = []
        for index, (competitor, dimension) in enumerate(sorted(actual)):
            for action, dependencies in [
                ("search", ()),
                ("fetch", (f"search-{index}",)),
                ("extract", (f"fetch-{index}",)),
            ]:
                tasks.append(
                    ResearchWorkItem(
                        id=f"{action}-{index}",
                        competitor=competitor,
                        dimension=dimension,
                        action=action,
                        query=f"{competitor} {dimension}",
                        depends_on=dependencies,
                    )
                )
        return ResearchPlan(brief=brief, tasks=tuple(tasks))


def fixture_semantic_adapter(candidate):
    """Explicit test double: copies a selected quote, never infers a real fact."""
    return Evidence(
        id="fixture-" + candidate.id,
        source=candidate.source,
        competitor=candidate.competitor,
        dimension=candidate.dimension,
        fact=candidate.quote,
        quote=candidate.quote,
        locator=candidate.locator.model_dump_json(),
        confidence=0.0,
        confidence_reason="SYNTHETIC FIXTURE ONLY; no calibrated or semantic confidence.",
    )


class ScriptedValidator:
    """Fixture oracle: supplies a declared verdict, not general conflict detection."""

    def __init__(self, case):
        self.case, self.calls = case, 0

    async def validate(self, plan, evidence):
        self.calls += 1
        expected = {
            (r["competitor"], r["dimension"], page["url"], page["quote"])
            for r in self.case["observations"]
            for page in r["pages"]
        }
        observed = {
            (e.competitor, e.dimension, str(e.source.url), e.quote) for e in evidence.evidence
        }
        assert expected == observed, (
            "Do not pass an oracle verdict for missing/altered source input"
        )
        return ValidationResult.model_validate(self.case["validator_oracle"])


class ScriptedAnalyst:
    """Only creates a fixture statement; does not compare actual products."""

    def __init__(self):
        self.calls = 0

    async def analyze(self, evidence):
        self.calls += 1
        return Analysis(
            facts=evidence.evidence,
            claims=(
                AnalysisClaim(
                    id="fixture-claim",
                    kind="comparison",
                    statement="SYNTHETIC: supplied sources contain these fixture observations.",
                    evidence_ids=tuple(e.id for e in evidence.evidence),
                    limitations=("SIMULATION ONLY: no production semantic analysis.",),
                ),
            ),
        )


class ScriptedReporter:
    """A temporary simulation report, not the production Reporter."""

    def __init__(self, case, bad_reference=False):
        self.case, self.bad_reference, self.calls = case, bad_reference, 0

    async def render(self, analysis, collections):
        self.calls += 1
        lines = [
            SIMULATION_HEADER,
            "",
            "Network, Planner, Validator, Analysis and Reporter use fixtures.",
            "Synthetic URLs below are identifiers, not verified public pages.",
            "",
        ]
        for evidence in analysis.facts:
            lines += [
                f"- [{evidence.id}] {evidence.quote}",
                f"  Source: {evidence.source.url}; "
                f"fetched_at: {evidence.source.fetched_at.isoformat()}",
            ]
        for collection in collections:
            lines += [
                f"- {collection.competitor} / {field.field_id}: {field.display_value}"
                for field in collection.fields
            ]
        if self.case.get("resolution_note"):
            lines += ["", self.case["resolution_note"]]
        ids = tuple(dict.fromkeys(e.source.id for e in analysis.facts))
        return ReportDraft(
            markdown="\n".join(lines),
            source_ids=("invented-source",) if self.bad_reference else ids,
        )


@dataclass
class SimulationResult:
    context: StateContext
    validation: Optional[ValidationResult]
    collections: tuple[CompetitorCollection, ...]
    report_path: Optional[Path]
    error: Optional[str]


class ReferenceWorkflow:
    """Test-only coordinator; src.Orchestrator is deliberately not patched."""

    def __init__(
        self,
        root,
        case,
        monkeypatch,
        *,
        fault=None,
        attempt="first",
        search_limit=30,
        capabilities=None,
        task_id=None,
    ):
        self.root, self.case, self.fault = root, case, fault
        self.attempt_root = root / attempt
        self.network = FixtureNetwork(case, fault)
        monkeypatch.setattr(web_search, "_http_worker", self.network.search_bytes)
        monkeypatch.setattr(web_fetch, "_fetch_worker", self.network.fetch_page)
        self.search_ledger = SearchLedger(root / "search.sqlite3")
        self.fetch_ledger = FetchLedger(root / "fetch.sqlite3")
        self.snapshots = SnapshotStore(root / "sources")
        self.registry = ToolRegistry()
        for tool in [
            BriefRecognitionTool(),
            WebSearchTool(ledger=self.search_ledger, max_calls=search_limit),
            WebFetchTool(ledger=self.fetch_ledger, snapshots=self.snapshots),
            EvidenceExtractionTool(snapshots=self.snapshots),
        ]:
            self.registry.register(tool)
        task_id = task_id or uuid5(NAMESPACE_URL, "simulation:" + case["request"])
        self.context = StateContext(
            task_id=task_id, user_input=case["request"], created_at=FIXED_TIME
        )
        self.tool_context = ToolContext(
            task_id=task_id,
            timeout_s=5,
            capabilities=frozenset(
                capabilities
                if capabilities is not None
                else {"brief_recognition", "web_search", "web_fetch", "evidence_extraction"}
            ),
        )
        self.memory = ResearchMemory(task_id=task_id)
        self.store = MemoryStore(self.attempt_root / "SIMULATION-memory.sqlite3")
        self.store.create(TaskSnapshot(context=self.context, memory=self.memory))
        self.validator, self.analyst = ScriptedValidator(case), ScriptedAnalyst()
        self.reporter = ScriptedReporter(case, bad_reference=fault == "bad_report_reference")
        self.collections, self.candidates, self.sources = [], [], {}
        self.validation = self.report_path = None

    def move(self, target, **kwargs):
        next_context = transition(self.context, target, **kwargs)
        self.store.commit(
            TaskSnapshot(context=next_context, memory=self.memory),
            expected_revision=self.context.revision,
        )
        self.context = next_context

    def update_memory(self, **values):
        self.memory = ResearchMemory.model_validate({**self.memory.model_dump(), **values})

    async def invoke(self, name, arguments):
        result = await self.registry.invoke(name, arguments, self.tool_context)
        if result.status != "ok":
            raise SimulationStop(result.error.code)
        return result.data

    def request_id(self, key):
        return uuid5(self.context.task_id, key)

    async def run(self):
        published_path = None
        try:
            self.move(ResearchState.UNDERSTANDING)
            recognized = BriefRecognition.model_validate(
                await self.invoke("recognize_brief", {"user_input": self.case["request"]})
            )
            if recognized.status != "ready":
                if recognized.questions[0].code != "entity_ambiguity":
                    raise SimulationStop("FIXTURE_BRIEF_NEEDS_INPUT")
                pending = PendingConfirmation(
                    reason="entity_ambiguity",
                    question=recognized.questions[0].question,
                    resume_state=ResearchState.PLANNING,
                    scope_hash=hashlib.sha256(self.case["request"].encode()).hexdigest(),
                )
                self.move(ResearchState.WAIT_CONFIRM, confirmation=pending)
                return self.result()
            self.update_memory(brief=recognized.brief)
            self.move(ResearchState.PLANNING)
            plan = FixturePlanner().plan(recognized.brief, self.case)
            self.update_memory(plan=plan)
            self.move(ResearchState.RESEARCHING)
            ids = tuple(dict.fromkeys(row["field_id"] for row in self.case["observations"]))
            template = build_collection_template(core_field_ids=ids)
            fields = {name: [] for name in plan.brief.competitors}
            for row in self.case["observations"]:
                query = row["competitor"] + " " + row["dimension"]
                found = await self.invoke(
                    "web_search",
                    {
                        "query": query,
                        "max_results": 5,
                        "request_id": self.request_id("search:" + query),
                    },
                )
                lookup = CollectionSearchAttempt(
                    query=query,
                    source_url=found["search_url"],
                    acquired_on=FIXED_TIME.date(),
                    outcome="checked" if found["results"] else "no_results",
                )
                if not found["results"]:
                    cell = CollectedField(
                        field_id=row["field_id"],
                        status="not_found",
                        search_attempts=(lookup,),
                        note="SYNTHETIC: complete fixture lookup had no source.",
                    )
                else:
                    items = []
                    for hit in found["results"]:
                        fetched = await self.invoke(
                            "web_fetch",
                            {
                                "url": hit["url"],
                                "request_id": self.request_id("fetch:" + hit["url"]),
                            },
                        )
                        source = fetched["document"]["source"]
                        stored = self.snapshots.load(source["snapshot_ref"])
                        expected = self.network.pages[hit["url"]]["quote"]
                        start = stored.text.index(expected)
                        if self.fault == "tampered_snapshot":
                            (
                                self.snapshots.root / source["snapshot_ref"][7:] / "raw.bin"
                            ).write_bytes(b"tampered")
                        extracted = await self.invoke(
                            "extract_features",
                            {
                                "snapshot_ref": source["snapshot_ref"],
                                "text_sha256": "0" * 64
                                if self.fault == "wrong_text_version"
                                else stored.document.text_sha256,
                                "competitor": row["competitor"],
                                "dimension": row["dimension"],
                                "spans": [{"start": start, "end": start + len(expected)}],
                            },
                        )
                        candidate = EvidenceCandidate.model_validate(extracted["candidates"][0])
                        verify_candidate(candidate, stored)
                        self.candidates.append(candidate)
                        self.sources[candidate.source.id] = candidate.source
                        items.append(
                            CollectedInformation(
                                value=candidate.quote,
                                nature="fact",
                                source_urls=(candidate.source.url,),
                                acquired_on=FIXED_TIME.date(),
                                confidence=0.0,
                                confidence_reason="SYNTHETIC FIXTURE ONLY.",
                                evidence_ids=("fixture-" + candidate.id,),
                            )
                        )
                    # Applicability is explicitly scripted, not inferred by production code.
                    cell = (
                        CollectedField(
                            field_id=row["field_id"],
                            status="not_applicable",
                            note="SYNTHETIC fixture-specific rationale: " + items[0].value,
                        )
                        if row["classification"] == "not_applicable"
                        else CollectedField(
                            field_id=row["field_id"],
                            status="found",
                            items=tuple(items),
                            search_attempts=(lookup,),
                        )
                    )
                fields[row["competitor"]].append(cell)
            self.collections = [
                CompetitorCollection(
                    template=template, competitor=name, as_of=FIXED_TIME.date(), fields=tuple(cells)
                )
                for name, cells in fields.items()
            ]
            evidence = EvidenceBundle(
                evidence=tuple(fixture_semantic_adapter(c) for c in self.candidates)
            )
            self.update_memory(sources=tuple(self.sources.values()), evidence=evidence.evidence)
            self.move(ResearchState.VALIDATING)
            self.validation = await self.validator.validate(plan, evidence)
            if self.validation.verdict != "PASS":
                self.move(ResearchState.PLANNING)
                return self.result()
            self.move(ResearchState.ANALYZING, validation=self.validation)
            analysis = await self.analyst.analyze(evidence)
            self.update_memory(analysis=analysis)
            self.move(ResearchState.REPORTING)
            draft = await self.reporter.render(analysis, self.collections)
            if set(draft.source_ids) != set(self.sources) or not draft.markdown.startswith(
                SIMULATION_HEADER
            ):
                raise SimulationStop("FIXTURE_REPORT_SOURCE_MISMATCH")
            path = self.attempt_root / "SIMULATION-report.md"
            temporary = self.attempt_root / ".SIMULATION-report.md.part"
            temporary_created = False
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    temporary_created = True
                    stream.write(draft.markdown)
                # Atomic publication without replacing an existing artifact.
                os.link(temporary, path)
                published_path = path
            finally:
                if temporary_created:
                    temporary.unlink()
            self.update_memory(report_refs=(str(path),))
            self.move(ResearchState.COMPLETED)
            self.report_path = path
            return self.result()
        except (SimulationStop, OSError) as error:
            if published_path is not None:
                published_path.unlink()
            self.report_path = None
            self.update_memory(report_refs=())
            self.move(
                ResearchState.FAILED,
                error_code=str(error)
                if isinstance(error, SimulationStop)
                else "FIXTURE_ARTIFACT_WRITE_FAILED",
            )
            return self.result(error=self.context.error_code)

    def result(self, error=None):
        return SimulationResult(
            self.context, self.validation, tuple(self.collections), self.report_path, error
        )
