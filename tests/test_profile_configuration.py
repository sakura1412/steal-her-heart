"""User-facing profile input, persisted identity and legacy compatibility."""

import json
import sqlite3
from uuid import uuid4

import pytest
from conftest import PROJECT_ANSWERS
from pydantic import ValidationError

from steal_her_heart.cli import main
from steal_her_heart.profile_configuration import (
    blank_profile,
    parse_profile,
    parse_readers,
    render_profile,
)
from steal_her_heart.project_initialization import (
    LEGACY_QUESTIONS,
    QUESTIONS,
    InterviewAnswer,
    ProjectInterview,
    ProjectStore,
)
from steal_her_heart.project_workspace import (
    LEGACY_EMPTY_PROFILE,
    ProjectWorkspace,
    legacy_profile_markdown,
)
from steal_her_heart.schemas import ProjectContext, ReaderProfile


def manual_profile():
    # Deliberately independent of the renderer: this is the user's pasted format.
    return """# 项目配置

## 我们的产品
- 名称与一句话描述：测试品牌，提供维修工具和现场服务
- 所属行业 / 业态（可多选）：工业工具、现场服务 / 硬件
- 目标客群：维修技师与工厂采购
- 市场范围：中国大陆与东南亚
- 核心优势：用户自述的易维修能力
- 主要短板：待确认
- 价格区间：CNY 800–2000/套；服务另计

## 分析目的
为下一季度产品组合与渠道投入提供依据

## 读者与关注点
| 读者 | 最关心什么 | 期望格式 |
|---|---|---|
| 渠道伙伴 | 服务覆盖；交付限制 | 两页中文渠道简报 |
| 采购委员会 | 成本和供应风险 | 对比表与附录 |

## 已知竞品（可为空）
- A, Inc.
- 品牌B

## 特别说明
避开匿名转载；价格保留地区口径；中文输出
"""


def completed_interview(**overrides):
    values = {**PROJECT_ANSWERS, **overrides}
    interview = ProjectInterview()
    for field in QUESTIONS:
        interview = interview.answer(field, values[field])
    return interview


def test_user_markdown_is_the_business_source_without_role_or_industry_whitelist():
    project_id = uuid4()
    context = parse_profile(manual_profile(), project_id)
    assert context.project_id == project_id
    assert context.industry == "工业工具、现场服务 / 硬件"
    assert context.industries == ("工业工具", "现场服务", "硬件")
    assert context.price_range == "CNY 800–2000/套；服务另计"
    assert context.special_notes == "避开匿名转载；价格保留地区口径；中文输出"
    assert context.known_competitors == ("A, Inc.", "品牌B")
    assert [r.label for r in context.readers] == ["渠道伙伴", "采购委员会"]
    assert context.readers[0].priorities == ("服务覆盖", "交付限制")
    assert context.readers[0].output_style == "两页中文渠道简报"
    assert context.uncertainties == ("weaknesses",)
    assert context.provenance == "user_provided"


def test_blank_and_example_only_profiles_stay_uninitialized():
    assert parse_profile(blank_profile(), uuid4()) is None
    text = manual_profile().replace(
        "为下一季度产品组合与渠道投入提供依据",
        "（例：规划下季度路线图 / 调整定价 / 准备销售话术 / 融资材料）",
    )
    assert parse_profile(text, uuid4()) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("测试品牌，提供维修工具和现场服务", "待填写"),
        ("工业工具、现场服务 / 硬件", "未知"),
        ("维修技师与工厂采购", ""),
        ("中国大陆与东南亚", "待确认"),
        ("服务覆盖；交付限制 | 两页中文渠道简报", "服务覆盖 |"),
        ("渠道伙伴 | 服务覆盖；交付限制", "渠道伙伴 | 待填写"),
    ],
)
def test_incomplete_manual_fields_do_not_open_initialization_gate(old, new):
    assert parse_profile(manual_profile().replace(old, new), uuid4()) is None


def test_rendered_profile_preserves_literals_and_only_hides_identity(initialized_project):
    values = initialized_project.model_dump()
    values.update(
        product="测试 <!-- literal --> & <tag> | 名称\n- 目标客群：这段是描述",
        objective="研究 <!-- objective --> 与 &#124; 原文\n第二行",
        special_notes="## 信息源\n保留 <!-- literal --> 与 |、&、< >\n```json\n{}\n```",
        known_competitors=("A, Inc.", "带|符号的名称", "含\n换行的名称"),
        readers=(
            ReaderProfile(
                id="custom-identity",
                label="渠道|伙伴",
                priorities=("质量与 <安全>", "成本"),
                output_style="一页|卡片\n中文",
            ),
        ),
    )
    context = ProjectContext.model_validate(values)
    text = render_profile(context)
    parsed = parse_profile(text, uuid4())
    for field in (
        "project_id",
        "product",
        "objective",
        "target_customers",
        "special_notes",
        "readers",
        "known_competitors",
    ):
        assert getattr(parsed, field) == getattr(context, field)
    metadata = json.loads(text.split("<!-- profile-meta: ")[1].split(" -->")[0])
    assert set(metadata) == {"schema_version", "project_id", "reader_ids"}
    assert "```json" not in text.splitlines()  # No separate business JSON block.


def test_manual_special_notes_keep_subheadings_and_fenced_business_heading():
    notes = "中文输出\n\n## 信息源限制\n禁止访问 example.com\n```json\n## 分析目的\n{}\n```"
    text = manual_profile().replace("避开匿名转载；价格保留地区口径；中文输出", notes)
    context = parse_profile(text, uuid4())
    assert context.special_notes == notes
    assert context.objective == "为下一季度产品组合与渠道投入提供依据"


def test_reader_identity_survives_reordering_and_custom_ids_are_preserved():
    context = parse_profile(manual_profile(), uuid4())
    first, second = context.readers
    values = context.model_dump()
    values["readers"] = (second, first)
    after = parse_profile(render_profile(ProjectContext.model_validate(values)), uuid4())
    assert after.project_id == context.project_id
    assert after.readers == (second, first)
    assert parse_profile(manual_profile(), context.project_id).readers == context.readers


@pytest.mark.parametrize(
    "value",
    [
        "采购 | 成本",
        "采购 | 成本 |",
        "采购 | | 表格",
        "采购 | 成本 | 表格 | 多余列",
        "待确认 | 待确认 | 待确认",
        "采购 | 未知 | 表格",
    ],
)
def test_interview_reasks_incomplete_reader_rows_instead_of_filling_default(value):
    interview = completed_interview(readers_and_priorities=value)
    assert interview.next_field == "readers_and_priorities"
    assert interview.context is None
    fixed = interview.answer("readers_and_priorities", "采购 | 成本 | 表格")
    assert fixed.context.readers[0].output_style == "表格"


def test_reader_duplicates_and_conflicting_identity_are_rejected():
    with pytest.raises(ValueError, match="labels"):
        parse_readers("采购 | 成本 | 表格；采购 | 风险 | 简报")
    with pytest.raises(ValueError, match="IDs"):
        parse_readers("采购 | 成本 | 表格；渠道 | 风险 | 简报", {"采购": "same", "渠道": "same"})


@pytest.mark.parametrize("concern", ["待确认；未知", "待确认；", "待填写；待确认"])
def test_a_list_of_unknown_reader_concerns_does_not_open_the_gate(concern):
    table = "| 读者 | 最关心什么 | 期望格式 |\n|---|---|---|\n| 采购 | " + concern + " | 表格 |"
    interview = completed_interview(readers_and_priorities=table)
    assert interview.next_field == "readers_and_priorities" and interview.context is None
    text = manual_profile().replace("服务覆盖；交付限制", concern)
    assert parse_profile(text, uuid4()) is None


def test_partial_unknown_reader_concern_and_format_keep_explicit_gaps():
    table = "| 读者 | 最关心什么 | 期望格式 |\n|---|---|---|\n| 采购 | 成本；待确认 | 待确认 |"
    interview = completed_interview(readers_and_priorities=table)
    for context in (interview.context, parse_profile(render_profile(interview.context), uuid4())):
        assert context.readers[0].priorities == ("成本", "待确认")
        assert context.readers[0].output_style == "待确认"
        assert {"reader_priorities", "reader_formats"} <= set(context.uncertainties)


@pytest.mark.parametrize("location", ["duplicate", "body"])
def test_metadata_must_have_one_identity_in_header(initialized_project, location):
    text = render_profile(initialized_project)
    metadata = next(line for line in text.splitlines() if line.startswith("<!-- profile-meta:"))
    if location == "duplicate":
        text += "\n" + metadata
    else:
        text = text.replace(metadata, "") + "\n" + metadata
    with pytest.raises(ValueError, match="identit"):
        parse_profile(text, uuid4())


@pytest.mark.parametrize("value,expected", [("", None), ("无", "无"), ("待确认", "待确认")])
def test_optional_answers_keep_absence_distinct_from_explicit_none_or_unknown(value, expected):
    interview = completed_interview(price_range=value, special_notes=value)
    context = interview.context
    reread = parse_profile(render_profile(context), uuid4())
    for result in (context, reread):
        assert result.price_range == expected and result.special_notes == expected
        assert ("price_range" in result.uncertainties) == (value != "无")
        assert ("special_notes" in result.uncertainties) == (value != "无")


def test_unknown_competitors_are_a_gap_without_becoming_candidate_names():
    text = manual_profile().replace("- A, Inc.\n- 品牌B", "待确认")
    context = parse_profile(text, uuid4())
    assert context.known_competitors == ()
    assert "known_competitors" in context.uncertainties
    reread = parse_profile(render_profile(context), uuid4())
    assert reread.known_competitors == ()
    assert "known_competitors" in reread.uncertainties


def test_manual_product_field_continuation_preserves_qualifications():
    text = manual_profile().replace(
        "用户自述的易维修能力", "响应快\n  支持本地部署，数据不出境\n  - 适用条件：仅中国大陆"
    )
    context = parse_profile(text, uuid4())
    assert context.strengths == "响应快\n  支持本地部署，数据不出境\n  - 适用条件：仅中国大陆"
    assert context.weaknesses == "待确认"


def test_placeholder_interview_answers_keep_gate_closed():
    interview = completed_interview(product="待填写")
    assert interview.context is None and interview.next_field == "product"


@pytest.mark.parametrize("command", ["/init-project", "project-status", "request"])
def test_partial_manual_profile_returns_actionable_guidance_without_starting_interview(
    tmp_path, capsys, command
):
    workspace = ProjectWorkspace(tmp_path, "partial")
    workspace.initialize_documents(ProjectInterview())
    path = workspace.path / "profile.md"
    partial = blank_profile().replace("- 名称与一句话描述：", "- 名称与一句话描述：用户已填产品")
    path.write_text(partial)
    args = ["--projects-root", str(tmp_path), "--project-name", "partial", command]
    if command == "request":
        args.append("/analyze 任意品牌")
    assert main(args) == 3
    result = capsys.readouterr()
    if command == "request":
        assert "继续补齐" in json.loads(result.out)["data"]["initialization_question"]
    else:
        assert "继续补齐" in result.err
    assert not workspace.interview_path.exists()
    assert path.read_text() == partial


def test_industry_list_is_derived_and_cannot_conflict_with_raw_answer(initialized_project):
    values = initialized_project.model_dump()
    values["industries"] = ("另一个行业",)
    with pytest.raises(ValidationError, match="Industry labels"):
        ProjectContext.model_validate(values)


def test_manually_filled_workspace_can_run_without_interview_database(tmp_path, capsys):
    root = tmp_path / "projects"
    workspace = ProjectWorkspace(root, "product-a")
    workspace.initialize_documents(ProjectInterview())
    path = workspace.path / "profile.md"
    path.write_text(manual_profile(), encoding="utf-8")
    prefix = ["--projects-root", str(root), "--project-name", "product-a"]
    assert main(prefix + ["project-status"]) == 0
    context = json.loads(capsys.readouterr().out)["context"]
    assert context["price_range"] == "CNY 800–2000/套；服务另计"
    assert context["readers"][0]["output_style"] == "两页中文渠道简报"
    assert main(prefix + ["/init-project"]) == 0
    again = json.loads(capsys.readouterr().out)["context"]
    assert context == again
    assert path.read_text() == manual_profile()
    assert not workspace.interview_path.exists()


def test_legacy_json_is_read_without_rewriting_or_inventing_new_answers(
    tmp_path, initialized_project
):
    workspace = ProjectWorkspace(tmp_path / "projects", "legacy")
    workspace.initialize_documents(ProjectInterview())
    values = initialized_project.model_dump(mode="json")
    for field in ("industries", "price_range", "special_notes", "readers"):
        values.pop(field)
    text = (
        "# 项目档案\n\n```json\n" + json.dumps({"context": values}, ensure_ascii=False) + "\n```\n"
    )
    path = workspace.path / "profile.md"
    path.write_text(text)
    before = path.read_bytes()
    context = workspace.read_required().context
    assert context.project_id == initialized_project.project_id
    assert context.price_range is None and context.special_notes is None
    assert context.readers == ()
    assert {"price_range", "special_notes", "reader_formats"} <= set(context.uncertainties)
    workspace.initialize_documents(completed_interview())
    assert path.read_bytes() == before


def test_mixed_legacy_and_markdown_business_fields_are_rejected(tmp_path, initialized_project):
    workspace = ProjectWorkspace(tmp_path, "mixed")
    workspace.initialize_documents(ProjectInterview())
    text = legacy_profile_markdown(initialized_project) + "\n## 我们的产品\n- 目标客群：新的客群\n"
    (workspace.path / "profile.md").write_text(text)
    with pytest.raises(ValueError, match="cannot be mixed"):
        workspace.read_required()


@pytest.mark.parametrize("edited", [False, True])
def test_only_untouched_legacy_empty_profile_can_upgrade_after_interview(tmp_path, edited):
    workspace = ProjectWorkspace(tmp_path, "legacy")
    workspace.initialize_documents(ProjectInterview())
    path = workspace.path / "profile.md"
    original = LEGACY_EMPTY_PROFILE + ("\n保留我的备注\n" if edited else "")
    path.write_text(original)
    workspace.initialize_documents(completed_interview())
    if edited:
        assert path.read_text() == original
        assert workspace.read_required().context is None
    else:
        assert path.read_text().startswith("# 项目配置\n")
        assert workspace.read_required().context.price_range == PROJECT_ANSWERS["price_range"]


@pytest.mark.parametrize("completed", [False, True])
def test_persisted_v1_interview_keeps_old_question_order_and_legacy_gaps(tmp_path, completed):
    fields = LEGACY_QUESTIONS if completed else LEGACY_QUESTIONS[:1]
    values = dict(PROJECT_ANSWERS, readers_and_priorities="投资人看市场机会和风险")
    legacy = ProjectInterview(
        schema_version=1,
        answers=tuple(InterviewAnswer(field=f, value=values[f]) for f in fields),
    )
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE project (singleton INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        db.execute("INSERT INTO project VALUES (1, ?)", (legacy.model_dump_json(),))
        db.execute("PRAGMA user_version=1")
    store = ProjectStore(path)
    loaded = store.load()
    assert loaded.schema_version == 1 and loaded.project_id == legacy.project_id
    if not completed:
        assert loaded.next_field == "target_customers"
        for field in LEGACY_QUESTIONS[1:]:
            loaded = store.answer(field, values[field])
    context = loaded.context
    assert context.readers == ()
    assert context.price_range is None and context.special_notes is None
    assert {"price_range", "special_notes", "reader_formats"} <= set(context.uncertainties)
    assert len(loaded.answers) == 9


def test_new_interactive_interview_maps_all_eleven_answers_to_correct_fields(
    tmp_path, monkeypatch, capsys
):
    answers = iter(PROJECT_ANSWERS.values())
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert (
        main(
            [
                "--projects-root",
                str(tmp_path),
                "--project-name",
                "new",
                "/init-project",
                "--interactive",
            ]
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["context"]
    for field in (
        "product",
        "industry",
        "target_customers",
        "market_scope",
        "strengths",
        "weaknesses",
        "price_range",
        "objective",
        "special_notes",
    ):
        assert context[field] == PROJECT_ANSWERS[field]
    assert context["known_competitors"] == []
    assert [r["output_style"] for r in context["readers"]] == ["功能矩阵", "投资备忘录"]
    saved = ProjectStore(tmp_path / "new/.state/project.sqlite3").load()
    assert saved.schema_version == 2 and len(saved.answers) == 11
