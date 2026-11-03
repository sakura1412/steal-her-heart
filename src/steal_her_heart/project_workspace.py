"""Product folders and mandatory profile/dimension reads for the project CLI."""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Optional
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .profile_configuration import parse_profile, parse_readers, render_profile
from .project_initialization import ProjectInterview
from .schemas import Contract, ProjectContext

DRAFT_DIMENSIONS = """# 项目研究维度

状态：待设计与用户确认，尚未锁定，不允许大规模采集。

先完成项目访谈和竞品发现确认，再从维度库选择核心字段与行业扩展。
后续阶段2将生成可审阅的项目维度与同源提案，并在用户确认后保存锁记录。
"""


class ProfileDocument(Contract):
    context: Optional[ProjectContext] = None


class WorkspaceInputs(Contract):
    profile_text: Annotated[str, Field(min_length=1, max_length=1048576)]
    dimensions_text: Annotated[str, Field(min_length=1, max_length=1048576)]
    profile_sha256: str
    dimensions_sha256: str
    context: Optional[ProjectContext] = None


def legacy_profile_markdown(context: Optional[ProjectContext]) -> str:
    document = ProfileDocument(context=context)
    return (
        "# 项目档案\n\n背景来自用户访谈，尚未作为外部证据核验。\n"
        "以下配置记录产品、客群、优势短板、行业、市场、分析目的及读者。\n"
        "context 为 null 时须继续 /init-project，不得自行补全。\n\n"
        "```json\n" + document.model_dump_json(indent=2) + "\n```\n"
    )


LEGACY_EMPTY_PROFILE = legacy_profile_markdown(None)


class ManualProfileIncomplete(ValueError):
    """The user's partial document must be completed without overwriting their edits."""


def profile_markdown(context: Optional[ProjectContext]) -> str:
    if context is not None and not context.readers:
        try:
            readers = parse_readers(context.readers_and_priorities)
        except ValueError:
            readers = ()
        if not readers:
            return legacy_profile_markdown(context)
    return render_profile(context)


class ProjectWorkspace:
    def __init__(self, root: Path, name: str) -> None:
        if not re.fullmatch(r"[\w][\w.-]{0,79}", name) or name in {".", ".."}:
            raise ValueError("Invalid project folder identifier")
        self.root = root.resolve()
        self.path = (self.root / name).resolve()
        if not self.path.is_relative_to(self.root):
            raise ValueError("Project folder escapes the projects root")
        self.interview_path = self.path / ".state" / "project.sqlite3"
        if not self.interview_path.resolve().is_relative_to(self.path):
            raise ValueError("Project state escapes the project directory")

    def _safe_file(self, name: str) -> Path:
        path = self.path / name
        if not path.resolve().is_relative_to(self.path):
            raise ValueError("Project document escapes the project directory")
        if path.exists() and not path.is_file():
            raise ValueError("Project document is not a regular file")
        return path

    def initialize_documents(self, interview: ProjectInterview) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        profile = self._safe_file("profile.md")
        if not profile.exists():
            profile.write_text(profile_markdown(interview.context))
        elif interview.context is not None and profile.read_text() in {
            profile_markdown(None),
            LEGACY_EMPTY_PROFILE,
        }:
            profile.write_text(profile_markdown(interview.context))
        dimensions = self._safe_file("dimensions.md")
        if not dimensions.exists():
            dimensions.write_text(DRAFT_DIMENSIONS)

    def require_interview_template(self) -> None:
        if self._safe_file("profile.md").read_text(encoding="utf-8") not in {
            profile_markdown(None),
            LEGACY_EMPTY_PROFILE,
        }:
            raise ManualProfileIncomplete(
                f"{self.path / 'profile.md'} 已有手工内容但配置未完成；"
                "请继续补齐该文件的必填字段和读者三列。现有内容会保留，"
                "当前版本不将访谈答案自动合并到手工档案。"
            )

    def read_required(self) -> WorkspaceInputs:
        contents = []
        for name in ("profile.md", "dimensions.md"):
            path = self._safe_file(name)
            if path.stat().st_size > 1048576:
                raise ValueError("Project document exceeds size limit")
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("Project document is empty")
            contents.append(text)
        profile, dimensions = contents
        if profile.splitlines()[0].strip() == "# 项目配置":
            context = parse_profile(profile, uuid5(NAMESPACE_URL, self.path.as_uri()))
        elif profile.splitlines()[0].strip() == "# 项目档案":
            if re.search(
                r"^## (我们的产品|分析目的|读者与关注点|已知竞品|特别说明)", profile, re.M
            ):
                raise ValueError("Legacy JSON and Markdown business fields cannot be mixed")
            blocks = re.findall(r"```json\s*\n(.*?)\n```", profile, re.S)
            if len(blocks) != 1:
                raise ValueError("Legacy profile needs exactly one JSON configuration block")
            context = ProfileDocument.model_validate_json(blocks[0]).context
            if context is not None:
                values = context.model_dump()
                gaps = tuple(
                    field
                    for field in ("price_range", "special_notes")
                    if getattr(context, field) is None
                )
                if not context.readers:
                    gaps += ("reader_formats",)
                values["uncertainties"] = tuple(dict.fromkeys(context.uncertainties + gaps))
                context = ProjectContext.model_validate(values)
        else:
            raise ValueError("Unrecognized project profile format")
        profile_digest = hashlib.sha256(profile.encode()).hexdigest()
        if context is not None:
            values = context.model_dump()
            values["profile_sha256"] = profile_digest
            context = ProjectContext.model_validate(values)
        return WorkspaceInputs(
            profile_text=profile,
            dimensions_text=dimensions,
            profile_sha256=profile_digest,
            dimensions_sha256=hashlib.sha256(dimensions.encode()).hexdigest(),
            context=context,
        )
