"""An opt-in SaaS recipe remains data, with checked references and no research."""

import copy
import json
import socket
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from steal_her_heart import research_presets
from steal_her_heart.collection_template import (
    BASE_FIELDS,
    CollectionTemplate,
    new_competitor_collection,
)
from steal_her_heart.research_presets import (
    DimensionRecipe,
    ResearchPreset,
    ReviewSamplingPolicy,
    SourceHint,
    load_research_preset,
)


def payload():
    return load_research_preset().model_dump(mode="json")


def resource(tmp_path, monkeypatch, body):
    directory = tmp_path / "presets"
    directory.mkdir()
    (directory / "saas.json").write_bytes(body)
    monkeypatch.setattr(research_presets.resources, "files", lambda package: tmp_path)


def test_packaged_saas_recipe_covers_each_field_once_and_roundtrips():
    preset = load_research_preset("saas")
    assert preset.id == preset.template.id == "saas"
    assert preset.version == preset.template.version == 1
    assert preset.execution == "configuration_only" and preset.research_ready is False
    assert len(preset.template.core_field_ids) == 15
    assert len(preset.template.fields) == len(preset.dimensions) == 25
    assert Counter(recipe.priority for recipe in preset.dimensions) == {
        "P0": 11,
        "P1": 12,
        "P2": 2,
    }
    assert ResearchPreset.model_validate_json(preset.model_dump_json()) == preset


def test_priority_groups_and_source_connections_are_complete():
    preset = load_research_preset()
    sources = {source.id: source for source in preset.sources}
    by_field = {recipe.field_id: recipe for recipe in preset.dimensions}
    assert {field.id for field in preset.template.fields} == set(by_field)
    for recipe in preset.dimensions:
        assert set(recipe.required_source_ids) <= sources.keys()
        assert recipe.questions and recipe.comparison_conditions and recipe.minimum_evidence
    assert all(
        by_field[field].priority == "P0"
        for field in (
            "positioning",
            "target_customers",
            "prices",
            "offer_variants",
            "feature_breadth",
            "feature_depth",
            "free_conversion_path",
        )
    )
    assert all(
        by_field[field].priority == "P1"
        for field in (
            "api_maturity",
            "integration_maturity",
            "data_portability",
            "switching_cost_analysis",
            "release_cadence",
            "review_sample_context",
        )
    )
    assert by_field["security_governance"].priority == "P2"


def test_field_nature_and_time_constraints_keep_facts_and_judgments_separate():
    fields = {field.id: field for field in load_research_preset().template.fields}
    assert fields["feature_breadth"].nature == fields["feature_depth"].nature == "fact"
    assert fields["data_portability"].nature == "fact"
    assert fields["switching_cost_analysis"].nature == "inference"
    assert fields["release_cadence"].nature == "inference"
    assert fields["release_cadence"].requires_time_window
    assert fields["recent_changes"].requires_time_window
    assert fields["review_sample_context"].requires_time_window
    assert fields["positive_reviews"].nature == fields["negative_reviews"].nature == "review"
    assert fields["api_maturity"].allow_not_applicable
    assert fields["security_governance"].allow_not_applicable
    assert not any(field.requires_own_product for field in fields.values())


def test_loading_does_not_change_the_universal_default_or_initialize_business_data():
    before = CollectionTemplate()
    preset = load_research_preset()
    collection = new_competitor_collection("任意硬件品牌", as_of=date(2026, 11, 4))
    assert collection.template == before == CollectionTemplate()
    assert before.fields == BASE_FIELDS
    assert before.industry is None and before.system_category is None
    assert "api_maturity" not in {field.id for field in collection.template.fields}
    assert collection.template.id != preset.id
    assert all(field.status == "pending" for field in collection.fields)


def test_loading_is_read_only_and_has_no_network_or_working_directory_dependency(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Loading a configuration must not connect or write")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    preset = load_research_preset()
    assert preset.id == "saas" and not list(tmp_path.iterdir())


def test_review_sources_disclose_access_modes_without_a_fake_scraper_or_product_url():
    preset = load_research_preset()
    sources = {source.id: source for source in preset.sources}
    assert preset.review_sampling.platform_ids == ("g2", "capterra")
    assert all(sources[name].source_type == "user_review" for name in ("g2", "capterra"))
    assert all(
        sources[name].access_mode == "manual_or_authorized_export" for name in ("g2", "capterra")
    )
    assert sources["g2_partner_api"].access_mode == "authenticated_api"
    assert sources["g2_partner_api"].notes
    assert all(
        sources[name].entry_url is None
        for name in (
            "official_site",
            "product_docs",
            "pricing",
            "api_docs",
            "migration_export",
        )
    )


def test_review_sampling_defaults_are_finite_suggestions_with_required_metadata():
    policy = load_research_preset().review_sampling
    assert policy.window_days == 365 and policy.target_per_rating_band == 3
    assert policy.rating_bands == ("low", "middle", "high")
    assert set(policy.required_metadata) == {
        "review_url",
        "published_on",
        "acquired_on",
        "rating",
        "role",
        "company_size",
        "incentivized",
        "verified",
        "text",
    }


@pytest.mark.parametrize(
    "name",
    [
        None,
        False,
        1,
        [],
        {},
        "",
        "SAAS",
        "saas ",
        "saas.json",
        "../saas",
        "/etc/passwd",
        "https://example.com/preset",
    ],
)
def test_unknown_or_path_like_names_never_access_resources(monkeypatch, name):
    def forbidden(*args):
        pytest.fail("Unregistered preset names must not access resources")

    monkeypatch.setattr(research_presets.resources, "files", forbidden)
    with pytest.raises(ValueError, match="未知研究配置"):
        load_research_preset(name)


@pytest.mark.parametrize(
    "change",
    [
        {"window_days": True},
        {"window_days": 0},
        {"window_days": 3651},
        {"target_per_rating_band": False},
        {"target_per_rating_band": 0},
        {"target_per_rating_band": 101},
        {"rating_bands": ["high", "low", "middle"]},
        {"required_metadata": ["review_url", "rating", "text"]},
        {"platform_ids": ["g2", "g2"]},
    ],
)
def test_sampling_policy_rejects_weakened_metadata_unbounded_targets_and_duplicates(change):
    with pytest.raises(ValidationError):
        ReviewSamplingPolicy.model_validate(change)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["sources"].append(copy.deepcopy(data["sources"][0])),
        lambda data: data["dimensions"].append(copy.deepcopy(data["dimensions"][0])),
        lambda data: data["dimensions"].pop(),
        lambda data: data["dimensions"][0].update(field_id="unknown"),
        lambda data: data["dimensions"][0].update(required_source_ids=["not-in-catalog"]),
        lambda data: data["dimensions"][0].update(required_source_ids=["pricing", "pricing"]),
        lambda data: data["dimensions"][0].update(questions=[]),
        lambda data: data["dimensions"][0].update(minimum_evidence=[]),
        lambda data: data["dimensions"][0].update(priority="P9"),
        lambda data: data["review_sampling"].update(platform_ids=["product_docs"]),
        lambda data: data["review_sampling"].update(platform_ids=["unknown"]),
        lambda data: data["template"]["fields"][0].update(nature="inference"),
        lambda data: data["template"].update(version=2),
        lambda data: data.update(execution="collecting"),
        lambda data: data.update(research_ready=True),
    ],
)
def test_recipe_references_and_core_constraints_are_enforced(mutation):
    data = payload()
    mutation(data)
    with pytest.raises(ValidationError):
        ResearchPreset.model_validate(data)


def test_models_are_immutable_and_do_not_accept_source_credentials():
    preset = load_research_preset()
    with pytest.raises(ValidationError):
        preset.version = 2
    with pytest.raises(ValidationError):
        preset.dimensions[0].priority = "P2"
    source = preset.sources[0].model_dump()
    source["entry_url"] = "https://user:password@example.com/"
    with pytest.raises(ValidationError):
        SourceHint.model_validate(source)


def test_preconstructed_template_cannot_bypass_shared_core_constraints():
    data = payload()
    template = load_research_preset().template
    altered = template.fields[0].model_copy(update={"nature": "inference"})
    data["template"] = template.model_copy(update={"fields": (altered,) + template.fields[1:]})
    with pytest.raises(ValidationError):
        ResearchPreset.model_validate(data)


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        b"null",
        b"\xff",
        b'{"id":"private-secret","id":"saas"}',
        b'{"version":NaN}',
        b"x" * 262145,
        b"[" * 5000,
    ],
)
def test_invalid_resource_is_a_safe_error_with_no_payload_exposure(tmp_path, monkeypatch, body):
    resource(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError, match="研究配置资源无效或不可用") as caught:
        load_research_preset()
    assert "private-secret" not in str(caught.value)


def test_missing_resource_and_mislabeled_resource_are_rejected_safely(tmp_path, monkeypatch):
    monkeypatch.setattr(research_presets.resources, "files", lambda package: tmp_path)
    with pytest.raises(ValueError, match="研究配置资源无效或不可用"):
        load_research_preset()
    data = payload_from_real_resource()
    data["id"] = data["template"]["id"] = "different-preset"
    resource(tmp_path, monkeypatch, json.dumps(data).encode())
    with pytest.raises(ValueError, match="研究配置资源无效或不可用"):
        load_research_preset()


def payload_from_real_resource():
    # This fixture bypasses only the patched lookup, never the model validation.
    path = Path(__file__).parents[1] / "src/steal_her_heart/presets/saas.json"
    return json.loads(path.read_text())


def test_a_source_and_recipe_are_not_evidence_or_executable_tool_requests():
    data = payload()
    assert not {"evidence", "source_snapshots", "competitors", "approved_by", "lock"} & set(data)
    recipe = data["dimensions"][0]
    with pytest.raises(ValidationError):
        DimensionRecipe.model_validate({**recipe, "execute": True})
