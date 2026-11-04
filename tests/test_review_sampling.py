"""Synthetic review metadata checks; no real reviews or platform access are asserted."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from steal_her_heart.research_presets import load_research_preset
from steal_her_heart.review_sampling import (
    PresetRequest,
    ResearchPresetTool,
    ReviewSampleBatch,
    assess_review_sample,
)
from steal_her_heart.schemas import ToolContext
from steal_her_heart.tools import ToolRegistry

AS_OF = date(2026, 11, 4)


def valid_batch_data():
    preset = load_research_preset()
    records, attempts = [], []
    for platform in preset.review_sampling.platform_ids:
        host = "www.g2.com" if platform == "g2" else "www.capterra.com"
        attempts.append(
            {
                "platform_id": platform,
                "outcome": "checked",
                "source_url": f"https://{host}/",
                "acquired_on": AS_OF,
                "scope": "Synthetic product, recent-first and low/middle/high bands",
            }
        )
        for band, rating in [("low", 1), ("middle", 3), ("high", 5)]:
            for n in range(preset.review_sampling.target_per_rating_band):
                identity = f"{platform}-{band}-{n}"
                records.append(
                    {
                        "id": identity,
                        "platform_id": platform,
                        "review_url": f"https://{host}/synthetic/{identity}",
                        "published_on": AS_OF - timedelta(days=30),
                        "acquired_on": AS_OF,
                        "rating": rating,
                        "role": "Operations",
                        "company_size": "11-50",
                        "incentivized": False,
                        "verified": False,
                        "text": f"Synthetic review text for {identity}; not an actual opinion.",
                    }
                )
    return {"records": records, "platform_attempts": attempts}


def assess(data):
    return assess_review_sample(
        load_research_preset(), ReviewSampleBatch.model_validate(data), as_of=AS_OF
    )


def codes(result):
    return {issue.code for issue in result.issues}


def test_balanced_metadata_coverage_is_not_fact_validation_or_population_statistics():
    data = valid_batch_data()
    result = assess(data)
    assert result.coverage_complete and result.facts_validated is False
    assert result.status == "metadata_assessment_only" and not result.issues
    assert all(item.observed == item.target for item in result.coverage)
    assert len(result.eligible_sample_ids) == len(data["records"])
    assert all(record["verified"] is False for record in data["records"])
    assert "PASS" not in result.model_dump_json()
    assert "population" in " ".join(result.limitations)


@pytest.mark.parametrize(
    "field",
    [
        "review_url",
        "published_on",
        "acquired_on",
        "rating",
        "role",
        "company_size",
        "incentivized",
        "verified",
        "text",
    ],
)
def test_missing_required_metadata_cannot_fill_a_sample_slot(field):
    data = valid_batch_data()
    data["records"][0][field] = None
    result = assess(data)
    assert not result.coverage_complete
    assert {"missing_metadata", "rating_band_shortfall"} <= codes(result)
    assert data["records"][0]["id"] in result.excluded_sample_ids


@pytest.mark.parametrize("marker", ["unknown", "未知", "未找到", "—", "未采集", "待确认"])
def test_explicit_unknown_context_is_a_gap_rather_than_invented_segment_data(marker):
    data = valid_batch_data()
    data["records"][0]["role"] = marker
    assert "missing_metadata" in codes(assess(data))


@pytest.mark.parametrize("outcome", ["unavailable", "not_found", "not_applicable"])
def test_access_or_listing_gaps_are_never_zero_reviews_or_success(outcome):
    data = valid_batch_data()
    data["platform_attempts"][0].update(outcome=outcome, note="Explicit synthetic gap")
    result = assess(data)
    assert not result.coverage_complete and "platform_" + outcome in codes(result)
    assert "unchecked_platform_sample" in codes(result)


def test_no_platform_summary_and_wrong_source_domain_are_reported():
    data = valid_batch_data()
    data["platform_attempts"] = data["platform_attempts"][1:]
    assert "platform_not_checked" in codes(assess(data))
    data = valid_batch_data()
    data["records"][0]["review_url"] = "https://www.g2.com.attacker.example/review"
    assert "review_url_mismatch" in codes(assess(data))
    data["platform_attempts"][0]["source_url"] = "https://example.com/"
    assert "platform_url_mismatch" in codes(assess(data))
    data = valid_batch_data()
    data["records"][0]["review_url"] = "https://www.g2.com:8080/reviews"
    assert "review_url_mismatch" in codes(assess(data))


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"published_on": AS_OF + timedelta(days=1)}, "invalid_review_dates"),
        ({"acquired_on": AS_OF + timedelta(days=1)}, "invalid_review_dates"),
        ({"published_on": AS_OF - timedelta(days=366)}, "outside_window"),
    ],
)
def test_window_and_date_consistency_exclude_records(change, expected):
    data = valid_batch_data()
    data["records"][0].update(change)
    assert expected in codes(assess(data))


def test_duplicate_url_or_cross_platform_text_only_counts_once_and_needs_review():
    data = valid_batch_data()
    data["records"][1]["review_url"] = data["records"][0]["review_url"]
    assert "duplicate_url" in codes(assess(data))
    data = valid_batch_data()
    data["records"][-1]["text"] = data["records"][0]["text"].upper() + "  "
    result = assess(data)
    assert "possible_duplicate_text" in codes(result)
    assert len(result.eligible_sample_ids) == len(data["records"]) - 1


def test_different_review_anchors_on_one_page_remain_distinct():
    data = valid_batch_data()
    for n in range(2):
        data["records"][n]["review_url"] = f"https://www.g2.com/product/reviews#review-{n}"
    assert assess(data).coverage_complete


def test_tracking_parameters_and_www_cannot_turn_one_review_into_two_slots():
    data = valid_batch_data()
    first = data["records"][0]
    data["records"][1]["review_url"] = first["review_url"].replace("www.", "") + "?utm_source=test"
    result = assess(data)
    assert "duplicate_url" in codes(result) and not result.coverage_complete


def test_identity_query_parameters_are_kept_for_different_reviews():
    data = valid_batch_data()
    for n in range(2):
        data["records"][n]["review_url"] = (
            f"https://www.g2.com/reviews?review_id={n}&utm_source=test"
        )
    assert assess(data).coverage_complete


def test_schema_rejects_rating_scales_duplicate_ids_and_unexplained_platform_gaps():
    for mutate in [
        lambda data: data["records"][0].update(rating=True),
        lambda data: data["records"][0].update(rating=10),
        lambda data: data["records"].append(data["records"][0]),
        lambda data: data["platform_attempts"][0].update(outcome="unavailable"),
    ]:
        data = valid_batch_data()
        mutate(data)
        with pytest.raises(ValidationError):
            ReviewSampleBatch.model_validate(data)


def test_assessment_is_deterministic_and_does_not_update_input_dates():
    batch = ReviewSampleBatch.model_validate(valid_batch_data())
    before = batch.model_dump_json()
    preset = load_research_preset()
    first = assess_review_sample(preset, batch, as_of=AS_OF)
    assert assess_review_sample(preset, batch, as_of=AS_OF) == first
    assert batch.model_dump_json() == before
    with pytest.raises(ValueError):
        assess_review_sample(preset, batch, as_of=date.min)


def test_preset_tool_requires_capability_and_matching_sample_date_before_execution():
    registry = ToolRegistry()
    registry.register(ResearchPresetTool())
    denied = asyncio.run(registry.invoke("research_preset", {}, ToolContext(task_id=uuid4())))
    assert denied.error.code == "PERMISSION_DENIED"
    context = ToolContext(task_id=uuid4(), capabilities=frozenset({"research_configuration"}))
    invalid = asyncio.run(registry.invoke("research_preset", {"as_of": AS_OF}, context))
    assert invalid.error.code == "INVALID_ARGUMENT"
    invalid_window = asyncio.run(
        registry.invoke("research_preset", {"as_of": date.min, "review_sample": {}}, context)
    )
    assert invalid_window.error.code == "INVALID_ARGUMENT"
    first = asyncio.run(registry.invoke("research_preset", {}, context))
    second = asyncio.run(registry.invoke("research_preset", {}, context))
    assert first.data == second.data and first.data["research_ready"] is False
    assert first.data["review_assessment"] is None
    with pytest.raises(ValidationError):
        PresetRequest(review_sample=ReviewSampleBatch())
