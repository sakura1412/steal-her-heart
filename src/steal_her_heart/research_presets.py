"""Optional research recipes, not collected facts, approval locks or validators."""

import json
from importlib import resources
from typing import Annotated, Literal, Optional

from pydantic import Field, HttpUrl, model_validator

from .collection_template import CollectionTemplate
from .schemas import Contract, Identifier, Text

Priority = Literal["P0", "P1", "P2"]
_Positive = Annotated[int, Field(strict=True, ge=1)]
_FILES = {"saas": "saas.json"}
_MAX_RESOURCE_BYTES = 262144


class SourceHint(Contract):
    id: Identifier
    label: Text
    source_type: Literal["official", "third_party", "user_review"]
    entry_url: Optional[HttpUrl] = None
    discovery_hint: Text
    access_mode: Literal["public_page", "manual_or_authorized_export", "authenticated_api"]
    notes: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def no_credentials(self) -> "SourceHint":
        if self.entry_url is not None and (
            self.entry_url.username is not None or self.entry_url.password is not None
        ):
            raise ValueError("Source hints must not contain credentials")
        return self


class DimensionRecipe(Contract):
    field_id: Identifier
    priority: Priority
    questions: tuple[Text, ...] = Field(min_length=1)
    required_source_ids: tuple[Identifier, ...] = Field(min_length=1)
    comparison_conditions: tuple[Text, ...] = Field(min_length=1)
    minimum_evidence: tuple[Text, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sources(self) -> "DimensionRecipe":
        if len(set(self.required_source_ids)) != len(self.required_source_ids):
            raise ValueError("Dimension source hints must be unique")
        return self


class ReviewSamplingPolicy(Contract):
    platform_ids: tuple[Identifier, ...] = Field(default=("g2", "capterra"), min_length=1)
    window_days: Annotated[int, Field(strict=True, ge=1, le=3650)] = 365
    target_per_rating_band: Annotated[int, Field(strict=True, ge=1, le=100)] = 3
    rating_bands: tuple[Literal["low"], Literal["middle"], Literal["high"]] = (
        "low",
        "middle",
        "high",
    )
    required_metadata: tuple[
        Literal["review_url"],
        Literal["published_on"],
        Literal["acquired_on"],
        Literal["rating"],
        Literal["role"],
        Literal["company_size"],
        Literal["incentivized"],
        Literal["verified"],
        Literal["text"],
    ] = (
        "review_url",
        "published_on",
        "acquired_on",
        "rating",
        "role",
        "company_size",
        "incentivized",
        "verified",
        "text",
    )

    @model_validator(mode="after")
    def unique_platforms(self) -> "ReviewSamplingPolicy":
        if len(set(self.platform_ids)) != len(self.platform_ids):
            raise ValueError("Review platforms must be unique")
        return self


class ResearchPreset(Contract):
    id: Identifier
    version: _Positive
    title: Text
    template: CollectionTemplate
    sources: tuple[SourceHint, ...] = Field(min_length=1)
    dimensions: tuple[DimensionRecipe, ...] = Field(min_length=1)
    review_sampling: ReviewSamplingPolicy
    execution: Literal["configuration_only"] = "configuration_only"
    research_ready: Literal[False] = False

    @model_validator(mode="after")
    def consistent_recipe(self) -> "ResearchPreset":
        # Core constraints also survive callers handing in already-created
        # nested model instances instead of the normal packaged JSON.
        template = CollectionTemplate.model_validate(self.template.model_dump())
        if self.id != template.id or self.version != template.version:
            raise ValueError("Preset identity and template version must match")
        sources = {source.id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Preset source IDs must be unique")
        field_ids = {field.id for field in template.fields}
        recipes = {recipe.field_id for recipe in self.dimensions}
        if len(recipes) != len(self.dimensions) or recipes != field_ids:
            raise ValueError("Every template field needs exactly one dimension recipe")
        if any(not set(recipe.required_source_ids) <= sources.keys() for recipe in self.dimensions):
            raise ValueError("Dimension refers to an unknown source hint")
        if any(
            source_id not in sources or sources[source_id].source_type != "user_review"
            for source_id in self.review_sampling.platform_ids
        ):
            raise ValueError("Review sampling must refer to user-review sources")
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise ValueError("Non-finite values are not valid configuration")


def load_research_preset(name: str = "saas") -> ResearchPreset:
    """Read a trusted packaged recipe; no network, writes or automatic selection.

    A caller explicitly chooses the preset. The industry-neutral collection
    template and project approval API are not changed by loading it.
    """
    if type(name) is not str or name not in _FILES:
        raise ValueError("未知研究配置；当前可选：saas。")
    try:
        body = (
            resources.files("steal_her_heart")
            .joinpath("presets")
            .joinpath(_FILES[name])
            .read_bytes()
        )
        if len(body) > _MAX_RESOURCE_BYTES:
            raise ValueError("Configuration resource is too large")
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        preset = ResearchPreset.model_validate(payload)
        if preset.id != name:
            raise ValueError("Configuration resource has the wrong identity")
        return preset
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise ValueError("研究配置资源无效或不可用；未开始采集。") from None
