"""Assess declared review metadata and sampling coverage, never semantic truth."""

import hashlib
import json
from datetime import date, timedelta
from typing import Annotated, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, HttpUrl, model_validator

from .research_presets import ResearchPreset, load_research_preset
from .schemas import Contract, Identifier, Text, ToolContext
from .tools import Tool, ToolExecutionError

Band = Literal["low", "middle", "high"]


class ReviewSample(Contract):
    id: Identifier
    platform_id: Identifier
    review_url: Optional[HttpUrl] = None
    published_on: Optional[date] = None
    acquired_on: Optional[date] = None
    rating: Optional[Annotated[float, Field(strict=True, ge=1, le=5)]] = None
    role: Optional[Text] = None
    company_size: Optional[Text] = None
    incentivized: Optional[Annotated[bool, Field(strict=True)]] = None
    verified: Optional[Annotated[bool, Field(strict=True)]] = None
    text: Optional[Text] = None


class PlatformReviewAttempt(Contract):
    platform_id: Identifier
    outcome: Literal["checked", "unavailable", "not_found", "not_applicable"]
    source_url: HttpUrl
    acquired_on: date
    scope: Text
    note: Optional[Text] = None

    @model_validator(mode="after")
    def explain_gap(self) -> "PlatformReviewAttempt":
        if self.outcome != "checked" and not self.note:
            raise ValueError("An unavailable or missing review source needs an explanation")
        return self


class ReviewSampleBatch(Contract):
    records: tuple[ReviewSample, ...] = Field(default=(), max_length=1000)
    platform_attempts: tuple[PlatformReviewAttempt, ...] = Field(default=(), max_length=20)
    rating_scale: Literal["1_to_5"] = "1_to_5"

    @model_validator(mode="after")
    def unique_records(self) -> "ReviewSampleBatch":
        if len({item.id for item in self.records}) != len(self.records):
            raise ValueError("Review record IDs must be unique")
        if len({item.platform_id for item in self.platform_attempts}) != len(
            self.platform_attempts
        ):
            raise ValueError("Use one declared collection summary per platform")
        return self


def parse_review_sample_json(payload: bytes) -> ReviewSampleBatch:
    """Reject contradictory duplicate JSON keys instead of silently choosing one."""

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate review input key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite review input")

    try:
        data = json.loads(payload, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
        return ReviewSampleBatch.model_validate(data)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ValueError("Review sample JSON is invalid or ambiguous") from None


class SamplingIssue(Contract):
    code: Identifier
    message: Text
    platform_id: Optional[Identifier] = None
    sample_id: Optional[Identifier] = None
    related_sample_id: Optional[Identifier] = None


class RatingBandCoverage(Contract):
    platform_id: Identifier
    band: Band
    observed: Annotated[int, Field(strict=True, ge=0)]
    target: Annotated[int, Field(strict=True, ge=1)]


class ReviewSamplingAssessment(Contract):
    status: Literal["metadata_assessment_only"] = "metadata_assessment_only"
    preset_id: Identifier
    preset_version: Annotated[int, Field(strict=True, ge=1)]
    as_of: date
    window_start: date
    assessed_records: Annotated[int, Field(strict=True, ge=0)]
    eligible_sample_ids: tuple[Identifier, ...]
    excluded_sample_ids: tuple[Identifier, ...]
    coverage: tuple[RatingBandCoverage, ...]
    issues: tuple[SamplingIssue, ...]
    coverage_complete: bool
    facts_validated: Literal[False] = False
    limitations: tuple[Text, ...] = (
        "Metadata and collection summaries are declared inputs, not verified tool execution logs.",
        "This does not check source snapshots, quote support, "
        "product identity or semantic conflicts.",
        "Balanced rating-band samples are not representative; do not infer population scores.",
        "Source access, dates and freshness require real collection and substantive validation.",
    )


def _unknown(value) -> bool:
    return value is None or (
        isinstance(value, str)
        and value.casefold()
        in {
            "unknown",
            "未知",
            "未提供",
            "未披露",
            "未找到",
            "未采集",
            "待确认",
            "待核实",
            "不详",
            "n/a",
            "—",
            "-",
        }
    )


def _matches_source(url: HttpUrl, expected: Optional[HttpUrl]) -> bool:
    if (
        expected is None
        or url.username is not None
        or url.password is not None
        or url.port != (443 if url.scheme == "https" else 80)
    ):
        return False
    base = (expected.host or "").lower().removeprefix("www.")
    host = (url.host or "").lower().rstrip(".")
    return bool(base) and (host == base or host.endswith("." + base))


def _review_identity_url(url: HttpUrl) -> str:
    parts = urlsplit(str(url))
    host = (url.host or "").lower().rstrip(".").removeprefix("www.")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"gclid", "fbclid", "msclkid"}
    ]
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))


def assess_review_sample(
    preset: ResearchPreset, batch: ReviewSampleBatch, *, as_of: date
) -> ReviewSamplingAssessment:
    """Fail sampling coverage on observable gaps, without claiming Validator PASS."""
    preset = ResearchPreset.model_validate(preset.model_dump())
    batch = ReviewSampleBatch.model_validate_json(batch.model_dump_json())
    if type(as_of) is not date:
        raise ValueError("Sampling as_of must be a date")
    policy = preset.review_sampling
    try:
        window_start = as_of - timedelta(days=policy.window_days - 1)
    except OverflowError:
        raise ValueError("Assessment date cannot cover the configured window") from None
    sources = {item.id: item for item in preset.sources}
    issues, eligible, excluded = [], [], []
    checked = set()
    counts = {
        (platform, band): 0 for platform in policy.platform_ids for band in policy.rating_bands
    }

    def issue(code, message, platform=None, sample=None, related=None):
        issues.append(
            SamplingIssue(
                code=code,
                message=message,
                platform_id=platform,
                sample_id=sample,
                related_sample_id=related,
            )
        )

    attempts = {item.platform_id: item for item in batch.platform_attempts}
    for attempt in batch.platform_attempts:
        if attempt.platform_id not in policy.platform_ids:
            issue(
                "unknown_platform",
                "Collection summary is outside the chosen policy.",
                attempt.platform_id,
            )
    for platform in policy.platform_ids:
        attempt = attempts.get(platform)
        source = sources[platform]
        if attempt is None:
            issue(
                "platform_not_checked",
                "No collection summary was supplied for this platform.",
                platform,
            )
        elif attempt.acquired_on > as_of:
            issue(
                "invalid_collection_date", "Collection date is after the assessment date.", platform
            )
        elif not _matches_source(attempt.source_url, source.entry_url):
            issue(
                "platform_url_mismatch",
                "Collection URL does not match the declared platform.",
                platform,
            )
        elif attempt.outcome != "checked":
            issue(
                "platform_" + attempt.outcome,
                "Platform access or sample availability has an explicit gap.",
                platform,
            )
        else:
            checked.add(platform)

    urls, texts = {}, {}
    for sample in batch.records:
        platform = sample.platform_id
        if platform not in policy.platform_ids:
            issue(
                "unknown_platform",
                "Review is outside the chosen platform policy.",
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        missing = [field for field in policy.required_metadata if _unknown(getattr(sample, field))]
        if missing:
            issue(
                "missing_metadata",
                "Missing or unknown review metadata: " + ", ".join(missing),
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        if platform not in checked:
            issue(
                "unchecked_platform_sample",
                "Review cannot fill coverage without a checked platform summary.",
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        if not _matches_source(sample.review_url, sources[platform].entry_url):
            issue(
                "review_url_mismatch",
                "Review URL does not match the declared platform.",
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        if sample.acquired_on > as_of or sample.published_on > sample.acquired_on:
            issue(
                "invalid_review_dates",
                "Review dates are inconsistent or after the assessment date.",
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        if not window_start <= sample.published_on <= as_of:
            issue(
                "outside_window",
                "Review publication date is outside the declared window.",
                platform,
                sample.id,
            )
            excluded.append(sample.id)
            continue
        # A review permalink can use a fragment to identify one review on a
        # shared product page. Different anchors are not automatically duplicates.
        url = _review_identity_url(sample.review_url)
        text_hash = hashlib.sha256(" ".join(sample.text.casefold().split()).encode()).hexdigest()
        duplicate = urls.get(url) or texts.get(text_hash)
        if duplicate:
            issue(
                "duplicate_url" if url in urls else "possible_duplicate_text",
                "Duplicate URL or identical normalized text does not fill "
                "a second sample slot; inspect manually.",
                platform,
                sample.id,
                duplicate,
            )
            excluded.append(sample.id)
            continue
        urls[url], texts[text_hash] = sample.id, sample.id
        band = "low" if sample.rating <= 2 else ("high" if sample.rating >= 4 else "middle")
        counts[(platform, band)] += 1
        eligible.append(sample.id)

    coverage = []
    for (platform, band), count in counts.items():
        coverage.append(
            RatingBandCoverage(
                platform_id=platform,
                band=band,
                observed=count,
                target=policy.target_per_rating_band,
            )
        )
        if count < policy.target_per_rating_band:
            issue(
                "rating_band_shortfall",
                f"{band} samples: {count}/{policy.target_per_rating_band}.",
                platform,
            )
    return ReviewSamplingAssessment(
        preset_id=preset.id,
        preset_version=preset.version,
        as_of=as_of,
        window_start=window_start,
        assessed_records=len(batch.records),
        eligible_sample_ids=tuple(eligible),
        excluded_sample_ids=tuple(excluded),
        coverage=tuple(coverage),
        issues=tuple(issues),
        coverage_complete=not issues,
    )


class PresetRequest(Contract):
    name: Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9-]{0,63}$")] = "saas"
    review_sample: Optional[ReviewSampleBatch] = None
    as_of: Optional[date] = None

    @model_validator(mode="after")
    def require_sample_date(self) -> "PresetRequest":
        if (self.review_sample is None) != (self.as_of is None):
            raise ValueError("Review sample and an explicit as_of date must be supplied together")
        return self


class PresetResponse(Contract):
    preset: ResearchPreset
    preset_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    review_assessment: Optional[ReviewSamplingAssessment] = None
    research_ready: Literal[False] = False


class ResearchPresetTool(Tool):
    name = "research_preset"
    description = "Load an optional research recipe and assess declared review sample metadata"
    input_model = PresetRequest
    output_model = PresetResponse
    required_capabilities = frozenset({"research_configuration"})

    async def run(self, arguments: PresetRequest, context: ToolContext) -> PresetResponse:
        try:
            preset = load_research_preset(arguments.name)
        except ValueError:
            raise ToolExecutionError(
                "PRESET_UNAVAILABLE", "Research preset is unknown or invalid."
            ) from None
        payload = json.dumps(
            preset.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        try:
            assessment = (
                assess_review_sample(preset, arguments.review_sample, as_of=arguments.as_of)
                if arguments.review_sample is not None
                else None
            )
        except ValueError:
            raise ToolExecutionError(
                "INVALID_ARGUMENT", "Sample metadata or assessment date cannot use this policy."
            ) from None
        return PresetResponse(
            preset=preset,
            preset_sha256=hashlib.sha256(payload).hexdigest(),
            review_assessment=assessment,
        )
