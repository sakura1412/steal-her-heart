from datetime import date

import pytest
from pydantic import ValidationError

from steal_her_heart.collection_template import (
    BASE_FIELDS,
    CollectedField,
    CollectedInformation,
    CollectionField,
    CollectionSearchAttempt,
    CollectionTemplate,
    CompetitorCollection,
    build_collection_template,
    new_competitor_collection,
)

DAY = date(2026, 11, 3)


def information(**changes):
    values = {
        "value": "来源支持的示例信息",
        "nature": "fact",
        "source_urls": ("https://example.com/product",),
        "acquired_on": DAY,
        "confidence": 0.7,
        "confidence_reason": "来源可定位，仍需语义核验",
        "evidence_ids": ("evidence-001",),
    }
    values.update(changes)
    return CollectedInformation(**values)


def replace_field(collection, replacement, **changes):
    payload = collection.model_dump()
    payload["fields"] = tuple(
        replacement if field.field_id == replacement.field_id else field
        for field in collection.fields
    )
    payload.update(changes)
    return CompetitorCollection.model_validate(payload)


def attempt(**changes):
    values = {
        "query": "品牌A 官方价格",
        "source_url": "https://example.com/pricing",
        "acquired_on": DAY,
        "outcome": "checked",
    }
    values.update(changes)
    return CollectionSearchAttempt(**values)


def test_new_template_keeps_all_fields_pending_without_fake_research():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    assert {f.field_id for f in collection.fields} == {f.id for f in collection.template.fields}
    assert {field.display_value for field in collection.fields} == {"未采集"}
    assert all(not field.items and not field.search_attempts for field in collection.fields)
    assert CompetitorCollection.model_validate_json(collection.model_dump_json()) == collection


def test_default_is_industry_neutral_and_has_no_vertical_feature_requirements():
    collection = new_competitor_collection("任意品牌", as_of=DAY)
    assert collection.template.id == "universal"
    assert collection.template.industry is None
    assert collection.template.system_category is None
    assert not {
        "lead_management",
        "customer_management",
        "opportunities_funnel",
        "quotes_contracts",
        "ai_capabilities",
        "mobile",
        "api_integrations",
        "win_loss_scenarios",
    } & {f.field_id for f in collection.fields}


@pytest.mark.parametrize(
    "industry,category,label",
    [
        ("制造业", "ERP", "生产排程"),
        ("物流", "WMS", "库位与出入库"),
        ("人力资源", "HR系统", "招聘与薪酬"),
        ("餐饮", "连锁品牌", "菜品与服务体验"),
        ("消费电子", "硬件", "续航与维修"),
        ("未预置的新行业", "自定义新品类", "用户指定的新维度"),
    ],
)
def test_arbitrary_categories_extend_the_same_contract(industry, category, label):
    template = build_collection_template(
        industry=industry,
        system_category=category,
        template_id="study-specific",
        extra_fields=(CollectionField(id="category_dimension", module="品类专项", label=label),),
    )
    first = new_competitor_collection("竞品A", as_of=DAY, template=template)
    second = new_competitor_collection("竞品B", as_of=DAY, template=template)
    assert first.template == second.template
    assert first.template.industry == industry
    assert first.template.system_category == category
    assert first.fields[-1].field_id == "category_dimension"
    assert len(first.fields) == len(BASE_FIELDS) + 1
    assert CompetitorCollection.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(ValidationError):
        replace_field(first, first.fields[0], fields=first.fields[:-1])


def test_template_snapshot_prevents_collisions_and_weakening_shared_constraints():
    with pytest.raises(ValidationError):
        build_collection_template(extra_fields=(BASE_FIELDS[0],))
    with pytest.raises(ValidationError):
        CollectionTemplate(fields=BASE_FIELDS[1:])
    with pytest.raises(ValidationError):
        CollectionTemplate(
            fields=(
                CollectionField(
                    id="positioning", module="基本盘", label="定位", nature="inference"
                ),
            )
            + BASE_FIELDS[1:]
        )


def test_category_fields_keep_metadata_and_explicit_time_constraints():
    template = build_collection_template(
        extra_fields=(
            CollectionField(
                id="seasonal_offering",
                module="自定义模块",
                label="季节服务变化",
                requires_time_window=True,
            ),
        )
    )
    collection = new_competitor_collection("任意竞品", as_of=DAY, template=template)
    with pytest.raises(ValidationError):
        CollectedField(field_id="seasonal_offering", status="found")
    field = CollectedField(field_id="seasonal_offering", status="found", items=(information(),))
    with pytest.raises(ValidationError):
        replace_field(collection, field)
    replace_field(collection, field, recent_window_start=date(2026, 10, 1))


def test_not_applicable_is_distinct_from_missing_information():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    with pytest.raises(ValidationError):
        CollectedField(field_id="reference_customers", status="not_applicable")
    field = CollectedField(
        field_id="reference_customers",
        status="not_applicable",
        note="本研究为个人消费品比较，计划确认不采集B2B代表客户",
    )
    assert replace_field(collection, field).fields[4].display_value == "不适用"
    with pytest.raises(ValidationError):
        replace_field(
            collection,
            CollectedField(
                field_id="positioning", status="not_applicable", note="不能凭空跳过定位"
            ),
        )


def test_old_vertical_schema_is_explicitly_rejected():
    payload = new_competitor_collection("品牌A", as_of=DAY).model_dump()
    payload.update(template_id="crm-sales", template_version=1)
    with pytest.raises(ValidationError):
        CompetitorCollection.model_validate(payload)


def test_missing_duplicate_and_unknown_fields_rejected():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    for fields in (
        collection.fields[:-1],
        collection.fields[:-1] + (collection.fields[0],),
        collection.fields[:-1] + (CollectedField(field_id="invented"),),
    ):
        with pytest.raises(ValidationError):
            CompetitorCollection(competitor="品牌A", as_of=DAY, fields=fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_urls": ()},
        {"source_urls": ("not-a-url",)},
        {"evidence_ids": ()},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"confidence_reason": ""},
        {"acquired_on": None},
        {"extra_metadata": "x"},
    ],
)
def test_every_information_item_requires_valid_metadata(changes):
    with pytest.raises(ValidationError):
        information(**changes)


def test_multiple_information_items_keep_individual_metadata():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    first = information()
    second = information(source_urls=("https://example.org/another-source",), confidence=0.4)
    found = CollectedField(field_id="positioning", status="found", items=(first, second))
    result = replace_field(collection, found)
    assert result.fields[0].items[0].confidence == 0.7
    assert result.fields[0].items[1].confidence == 0.4
    assert result.fields[0].items[0].source_urls != result.fields[0].items[1].source_urls


def test_not_found_requires_executed_lookup_and_displays_exact_user_text():
    for kwargs in (
        {},
        {"note": "未查到"},
        {"note": "站点不可访问", "search_attempts": (attempt(outcome="unavailable"),)},
    ):
        with pytest.raises(ValidationError):
            CollectedField(field_id="prices", status="not_found", **kwargs)
    field = CollectedField(
        field_id="prices",
        status="not_found",
        search_attempts=(attempt(),),
        note="本次只检查官网定价页",
    )
    assert field.display_value == "未找到"
    assert not field.items


def test_pending_and_not_found_cannot_smuggle_facts_and_found_requires_items():
    for status in ("pending", "not_found"):
        with pytest.raises(ValidationError):
            CollectedField(field_id="positioning", status=status, items=(information(),))
    with pytest.raises(ValidationError):
        CollectedField(field_id="positioning", status="found")


def test_reviews_and_analyses_cannot_be_mislabeled_as_facts():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    for field_id in ("positive_reviews", "limitations"):
        with pytest.raises(ValidationError):
            replace_field(
                collection,
                CollectedField(field_id=field_id, status="found", items=(information(),)),
            )
    replace_field(
        collection,
        CollectedField(
            field_id="positive_reviews", status="found", items=(information(nature="review"),)
        ),
    )


def test_win_loss_requires_our_product_before_claiming_a_result():
    template = build_collection_template(
        extra_fields=(
            CollectionField(
                id="win_loss_scenarios",
                module="竞争与市场表现",
                label="相对我方的适用场景",
                nature="inference",
                requires_own_product=True,
            ),
        )
    )
    collection = new_competitor_collection("品牌A", as_of=DAY, template=template)
    field = CollectedField(
        field_id="win_loss_scenarios", status="found", items=(information(nature="inference"),)
    )
    with pytest.raises(ValidationError):
        replace_field(collection, field)
    assert replace_field(collection, field, own_product="我们的产品").own_product == "我们的产品"


def test_recent_updates_need_a_window_and_dates_cannot_exceed_cutoff():
    collection = new_competitor_collection("品牌A", as_of=DAY)
    recent = CollectedField(field_id="recent_changes", status="found", items=(information(),))
    with pytest.raises(ValidationError):
        replace_field(collection, recent)
    replace_field(collection, recent, recent_window_start=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        new_competitor_collection("品牌A", as_of=DAY, recent_window_start=date(2026, 12, 1))
    with pytest.raises(ValidationError):
        replace_field(
            collection,
            CollectedField(
                field_id="positioning",
                status="found",
                items=(information(acquired_on=date(2026, 11, 4)),),
            ),
        )
