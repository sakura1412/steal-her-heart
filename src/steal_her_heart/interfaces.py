"""Ports for later phases; declarations only, no hidden model/tool execution."""

from typing import Protocol

from .brief_recognition import BriefRecognition
from .schemas import (
    Analysis,
    EvidenceBundle,
    ReportDraft,
    ResearchBrief,
    ResearchPlan,
    TaskRequest,
    ValidationResult,
)


class BriefRecognizer(Protocol):
    async def understand(self, request: TaskRequest) -> BriefRecognition: ...


class Planner(BriefRecognizer, Protocol):
    async def plan(self, brief: ResearchBrief) -> ResearchPlan: ...


class ResearchEngine(Protocol):
    async def collect(self, plan: ResearchPlan) -> EvidenceBundle: ...


class Validator(Protocol):
    async def validate(self, plan: ResearchPlan, evidence: EvidenceBundle) -> ValidationResult: ...


class AnalysisEngine(Protocol):
    async def analyze(self, evidence: EvidenceBundle) -> Analysis: ...


class ReportGenerator(Protocol):
    async def render(self, analysis: Analysis) -> ReportDraft: ...
