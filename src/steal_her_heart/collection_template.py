"""Industry-neutral collection core and configurable category dimensions."""

from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import Field, HttpUrl, model_validator

from .schemas import Contract, Identifier, Text

Nature = Literal["fact", "review", "inference"]


class CollectionField(Contract):
    id: Identifier
    module: Text
    label: Text
    nature: Nature = "fact"
    requires_own_product: bool = False
    requires_time_window: bool = False
    allow_not_applicable: bool = False


BASE_FIELDS = (
    CollectionField(id="positioning", module="基本盘", label="定位"),
    CollectionField(id="target_customers", module="基本盘", label="目标客户／用户与使用场景"),
    CollectionField(id="scale", module="基本盘", label="规模"),
    CollectionField(id="background", module="基本盘", label="主体背景／发展历程"),
    CollectionField(
        id="reference_customers",
        module="基本盘",
        label="代表客户／典型使用者",
        allow_not_applicable=True,
    ),
    CollectionField(id="core_offering", module="产品与服务", label="核心产品、能力或服务内容"),
    CollectionField(id="delivery_usage", module="产品与服务", label="交付、使用或服务方式"),
    CollectionField(id="offer_variants", module="价格与商业模式", label="产品／服务／版本划分"),
    CollectionField(
        id="prices", module="价格与商业模式", label="价格／报价", allow_not_applicable=True
    ),
    CollectionField(id="billing", module="价格与商业模式", label="计费／交易与商业模式"),
    CollectionField(
        id="trial_experience",
        module="价格与商业模式",
        label="试用／体验／售后政策",
        allow_not_applicable=True,
    ),
    CollectionField(id="value_proposition", module="竞争与市场表现", label="差异化卖点／价值主张"),
    CollectionField(id="public_messaging", module="竞争与市场表现", label="公开宣传／传播表述"),
    CollectionField(
        id="limitations", module="竞争与市场表现", label="局限与适用边界", nature="inference"
    ),
    CollectionField(id="positive_reviews", module="用户口碑", label="好评点", nature="review"),
    CollectionField(id="negative_reviews", module="用户口碑", label="差评点", nature="review"),
    CollectionField(
        id="recurring_complaints", module="用户口碑", label="高频抱怨", nature="review"
    ),
    CollectionField(
        id="recent_changes",
        module="近期动态",
        label="近期产品／服务／经营变化",
        requires_time_window=True,
    ),
    CollectionField(
        id="recent_funding",
        module="近期动态",
        label="融资／资本动态",
        requires_time_window=True,
        allow_not_applicable=True,
    ),
    CollectionField(
        id="recent_partnerships", module="近期动态", label="合作", requires_time_window=True
    ),
    CollectionField(
        id="recent_campaigns", module="近期动态", label="营销活动", requires_time_window=True
    ),
)


class CollectionTemplate(Contract):
    id: Identifier = "universal"
    version: Annotated[int, Field(strict=True, ge=1)] = 1
    industry: Optional[Text] = None
    system_category: Optional[Text] = None
    core_field_ids: tuple[Identifier, ...] = Field(
        default=tuple(field.id for field in BASE_FIELDS), min_length=1
    )
    fields: tuple[CollectionField, ...] = BASE_FIELDS

    @model_validator(mode="after")
    def check_definitions(self) -> "CollectionTemplate":
        definitions = {field.id: field for field in self.fields}
        if len(definitions) != len(self.fields):
            raise ValueError("Template field IDs must be unique")
        core = {field.id: field for field in BASE_FIELDS}
        selected = set(self.core_field_ids)
        if len(selected) != len(self.core_field_ids) or not selected <= set(core):
            raise ValueError("Choose unique core dimensions from the library")
        if any(definitions.get(field_id) != core[field_id] for field_id in selected):
            raise ValueError("Selected core definitions and constraints must remain intact")
        if (set(definitions) & set(core)) != selected:
            raise ValueError("Template includes unselected core dimensions")
        return self


def build_collection_template(
    *,
    industry: Optional[str] = None,
    system_category: Optional[str] = None,
    extra_fields: tuple[CollectionField, ...] = (),
    template_id: str = "universal",
    version: int = 1,
    core_field_ids: Optional[tuple[str, ...]] = None,
) -> CollectionTemplate:
    """Compose category dimensions without a whitelist or changes to the collection engine."""
    selected = core_field_ids if core_field_ids is not None else tuple(f.id for f in BASE_FIELDS)
    core = {field.id: field for field in BASE_FIELDS}
    if not set(selected) <= set(core):
        raise ValueError("Unknown core dimension")
    return CollectionTemplate(
        id=template_id,
        version=version,
        industry=industry,
        system_category=system_category,
        core_field_ids=selected,
        fields=tuple(core[field_id] for field_id in selected) + extra_fields,
    )


class CollectedInformation(Contract):
    """Metadata belongs to every claim, not to an entire competitor or report."""

    value: Text
    nature: Nature
    source_urls: tuple[HttpUrl, ...] = Field(min_length=1)
    acquired_on: date
    confidence: Annotated[float, Field(strict=True, ge=0, le=1)]
    confidence_reason: Text
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)


class CollectionSearchAttempt(Contract):
    query: Text
    source_url: HttpUrl
    acquired_on: date
    outcome: Literal["checked", "no_results", "unavailable"]


class CollectedField(Contract):
    field_id: Identifier
    status: Literal["pending", "found", "not_found", "not_applicable"] = "pending"
    items: tuple[CollectedInformation, ...] = ()
    search_attempts: tuple[CollectionSearchAttempt, ...] = ()
    note: Optional[Text] = None

    @model_validator(mode="after")
    def check_status(self) -> "CollectedField":
        if self.status == "found":
            if not self.items:
                raise ValueError("Found fields need source-backed information")
        elif self.items:
            raise ValueError("Only found fields may contain factual values")
        if self.status == "not_applicable" and not self.note:
            raise ValueError("Not applicable needs an explicit applicability rationale")
        if self.status == "not_found" and (
            not self.note
            or not any(a.outcome in {"checked", "no_results"} for a in self.search_attempts)
        ):
            raise ValueError("Not found needs a search scope note and a completed lookup")
        return self

    @property
    def display_value(self) -> str:
        if self.status == "not_found":
            return "未找到"
        if self.status == "pending":
            return "未采集"
        if self.status == "not_applicable":
            return "不适用"
        return "；".join(item.value for item in self.items)


class CompetitorCollection(Contract):
    schema_version: Literal[2] = 2
    template: CollectionTemplate = Field(default_factory=CollectionTemplate)
    competitor: Text
    as_of: date
    own_product: Optional[Text] = None
    recent_window_start: Optional[date] = None
    fields: tuple[CollectedField, ...]

    @model_validator(mode="after")
    def check_template(self) -> "CompetitorCollection":
        specs = {field.id: field for field in self.template.fields}
        ids = [field.field_id for field in self.fields]
        if len(ids) != len(set(ids)) or set(ids) != set(specs):
            raise ValueError(
                "Every template field must appear exactly once; unknown fields rejected"
            )
        if self.recent_window_start and self.recent_window_start > self.as_of:
            raise ValueError("Recent research window cannot start after as_of")
        for field in self.fields:
            spec = specs[field.field_id]
            if field.status == "not_applicable" and not spec.allow_not_applicable:
                raise ValueError("This field cannot be dismissed as not applicable")
            if spec.requires_own_product and not self.own_product and field.status != "pending":
                raise ValueError("Win/loss scenarios require our own product context")
            if any(item.nature != spec.nature for item in field.items):
                raise ValueError("Facts, reviews and inferences must remain distinct")
            if any(item.acquired_on > self.as_of for item in field.items) or any(
                attempt.acquired_on > self.as_of for attempt in field.search_attempts
            ):
                raise ValueError("Information cannot be acquired after the collection cutoff")
            if (
                spec.requires_time_window
                and field.status in {"found", "not_found"}
                and not self.recent_window_start
            ):
                raise ValueError("Recent updates require an explicit research time window")
        return self


def new_competitor_collection(
    competitor: str,
    *,
    as_of: date,
    template: Optional[CollectionTemplate] = None,
    own_product: Optional[str] = None,
    recent_window_start: Optional[date] = None,
) -> CompetitorCollection:
    """Create pending cells from the exact template snapshot, for any category."""
    selected = template if template is not None else CollectionTemplate()
    return CompetitorCollection(
        template=selected,
        competitor=competitor,
        as_of=as_of,
        own_product=own_product,
        recent_window_start=recent_window_start,
        fields=tuple(CollectedField(field_id=field.id) for field in selected.fields),
    )
