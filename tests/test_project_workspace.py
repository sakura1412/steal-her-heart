import json

import pytest
from conftest import PROJECT_ANSWERS
from test_research_workflow import planned

from steal_her_heart.cli import main
from steal_her_heart.project_initialization import ProjectInterview
from steal_her_heart.project_workspace import DRAFT_DIMENSIONS, ProjectWorkspace, profile_markdown
from steal_her_heart.research_workflow import write_dimensions
from steal_her_heart.schemas import ProjectContext


def completed_interview():
    interview = ProjectInterview()
    for field, value in PROJECT_ANSWERS.items():
        interview = interview.answer(field, value)
    return interview


def test_init_creates_required_files_without_faking_completed_dimensions(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "projects", "任意产品")
    workspace.initialize_documents(ProjectInterview())
    inputs = workspace.read_required()
    assert inputs.context is None
    assert "尚未锁定" in inputs.dimensions_text
    workspace.initialize_documents(completed_interview())
    inputs = workspace.read_required()
    assert inputs.context.profile_sha256 == inputs.profile_sha256
    assert inputs.context.known_competitors == ()


def test_missing_documents_and_project_boundary_are_enforced(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "projects", "a")
    with pytest.raises(FileNotFoundError):
        workspace.read_required()
    for name in ("../outside", ".", "..", "a/b"):
        with pytest.raises(ValueError):
            ProjectWorkspace(tmp_path / "projects", name)
    workspace.path.mkdir(parents=True)
    external = tmp_path / "external.md"
    external.write_text("external")
    (workspace.path / "profile.md").symlink_to(external)
    with pytest.raises(ValueError):
        workspace.read_required()


def test_product_profiles_are_independent_and_edits_change_context_fingerprint(tmp_path):
    first, second = (ProjectWorkspace(tmp_path / "projects", name) for name in ("a", "b"))
    first.initialize_documents(completed_interview())
    second.initialize_documents(completed_interview())
    before = first.read_required()
    untouched = second.read_required()
    profile = first.path / "profile.md"
    profile.write_text(profile.read_text() + "\n补充：本次只研究海外市场。\n")
    first.initialize_documents(completed_interview())
    assert first.read_required().context != before.context
    assert second.read_required() == untouched


def test_default_cli_layout_and_slash_init_alias(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["--project-name", "product-a", "/init-project"]) == 3
    capsys.readouterr()
    folder = tmp_path / "projects/product-a"
    assert (folder / "profile.md").exists() and (folder / "dimensions.md").exists()
    for field, value in PROJECT_ANSWERS.items():
        assert main(["--project-name", "product-a", "answer-project", field, value]) == (
            0 if field == next(reversed(PROJECT_ANSWERS)) else 3
        )
        capsys.readouterr()
    assert main(["--project-name", "product-a", "request", "/analyze 任意品牌"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["execution"] == "pending_module"
    (folder / "dimensions.md").unlink()
    assert main(["--project-name", "product-a", "request", "/analyze 任意品牌"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["execution"] == "initialization_required"
    assert "dimensions.md" in payload["data"]["initialization_question"]


def test_stage_two_replaces_only_the_initial_dimension_placeholder(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "projects", "a")
    workspace.initialize_documents(completed_interview())
    inputs = workspace.read_required()
    proposed, approval, dimensions, _ = planned(inputs.context)
    assert (workspace.path / "dimensions.md").read_text() == DRAFT_DIMENSIONS
    write_dimensions(
        workspace.path / "dimensions.md", inputs.context, proposed, approval, dimensions
    )
    assert "industry_quality" in (workspace.path / "dimensions.md").read_text()
    with pytest.raises(FileExistsError):
        write_dimensions(
            workspace.path / "dimensions.md", inputs.context, proposed, approval, dimensions
        )


def test_profile_configuration_is_read_and_not_overwritten_without_sqlite(tmp_path, capsys):
    workspace = ProjectWorkspace(tmp_path / "projects", "a")
    workspace.initialize_documents(completed_interview())
    values = completed_interview().context.model_dump()
    values["objective"] = "修改后的投资判断目的"
    profile = workspace.path / "profile.md"
    profile.write_text(profile_markdown(ProjectContext.model_validate(values)))
    before = profile.read_text()
    prefix = ["--projects-root", str(tmp_path / "projects"), "--project-name", "a"]
    assert main(prefix + ["project-status"]) == 0
    assert json.loads(capsys.readouterr().out)["context"]["objective"] == "修改后的投资判断目的"
    assert main(prefix + ["/init-project"]) == 0
    capsys.readouterr()
    assert profile.read_text() == before
    assert not workspace.interview_path.exists()


def test_profile_edit_prevents_silent_task_resume(tmp_path, capsys):
    workspace = ProjectWorkspace(tmp_path / "projects", "a")
    workspace.initialize_documents(completed_interview())
    prefix = ["--projects-root", str(tmp_path / "projects"), "--project-name", "a"]
    assert main(prefix + ["run", "分析任意品牌"]) == 3
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    profile = workspace.path / "profile.md"
    profile.write_text(profile.read_text() + "\n新增市场约束。\n")
    assert main(prefix + ["resume", task_id]) == 2
    assert "Invalid input" in capsys.readouterr().err
