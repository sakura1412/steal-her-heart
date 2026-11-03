"""Stage 0: resumable user interview, kept separate from research evidence."""

import re
import sqlite3
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .profile_configuration import missing, parse_readers, reader_table, reader_uncertainties
from .schemas import UNKNOWN_PROJECT_VALUES, Contract, ProjectContext, Text

QuestionID = Literal[
    "product",
    "target_customers",
    "strengths",
    "weaknesses",
    "industry",
    "market_scope",
    "objective",
    "readers_and_priorities",
    "known_competitors",
    "price_range",
    "special_notes",
]
QUESTIONS = {
    "product": "你的产品叫什么？主要解决什么问题、提供什么产品或服务？",
    "industry": "所属行业／业态是什么？可多选，用顿号分隔，不限软件或特定行业。",
    "target_customers": "主要卖给谁、由谁使用？请说明目标客群和典型场景。",
    "market_scope": "研究面向国内、海外还是特定地区？请说明市场范围。",
    "strengths": "你认为目前的核心优势是什么？尚不清楚可以明确写“待确认”。",
    "weaknesses": "目前有哪些短板或限制？尚不清楚可以明确写“待确认”。",
    "price_range": "产品价格区间是什么？请保留币种、计费单位等口径；未知可写待确认或留空。",
    "objective": "这次分析要支持什么决策：路线图、定价、销售话术、投资判断或其他？",
    "readers_and_priorities": (
        "读者是谁、关注什么、期望什么格式？每项按“读者 | 关注点 | 格式”，多项用分号分隔。"
    ),
    "known_competitors": "有哪些已知竞品？可用顿号分隔；没有可直接留空或写“无”。",
    "special_notes": "有要避开的信息源、监管限制、语言偏好或其他特别说明吗？可留空或明确写无。",
}
LEGACY_QUESTIONS = (
    "product",
    "target_customers",
    "strengths",
    "weaknesses",
    "industry",
    "market_scope",
    "objective",
    "readers_and_priorities",
    "known_competitors",
)


class InterviewAnswer(Contract):
    field: QuestionID
    value: str = Field(strict=True, max_length=4000)

    @model_validator(mode="after")
    def check_empty(self) -> "InterviewAnswer":
        if not self.value and self.field not in {
            "known_competitors",
            "price_range",
            "special_notes",
        }:
            raise ValueError("This interview question cannot be left blank")
        return self


class ProjectInterview(Contract):
    schema_version: Literal[1, 2] = 2
    project_id: UUID = Field(default_factory=uuid4)
    answers: tuple[InterviewAnswer, ...] = ()

    @model_validator(mode="after")
    def check_unique(self) -> "ProjectInterview":
        if len({a.field for a in self.answers}) != len(self.answers):
            raise ValueError("Interview fields must be unique")
        return self

    @property
    def next_field(self) -> Optional[QuestionID]:
        answers = {a.field: a.value for a in self.answers}
        fields = LEGACY_QUESTIONS if self.schema_version == 1 else tuple(QUESTIONS)
        for field in fields:
            if field not in answers:
                return field
        for field, value in answers.items():
            if field not in {"known_competitors", "price_range", "special_notes"} and missing(
                value
            ):
                return field
            if field not in {
                "strengths",
                "weaknesses",
                "known_competitors",
                "price_range",
                "special_notes",
            } and (value.casefold() in UNKNOWN_PROJECT_VALUES):
                return field
        if self.schema_version == 2:
            try:
                if not parse_readers(answers["readers_and_priorities"]):
                    return "readers_and_priorities"
            except ValueError:
                return "readers_and_priorities"
        return None

    def answer(self, field: QuestionID, value: str) -> "ProjectInterview":
        if field != self.next_field:
            raise ValueError("Answer must match the currently pending question")
        new = InterviewAnswer(field=field, value=value)
        values = tuple(a for a in self.answers if a.field != field) + (new,)
        return ProjectInterview(
            schema_version=self.schema_version, project_id=self.project_id, answers=values
        )

    @property
    def context(self) -> Optional[ProjectContext]:
        if self.next_field is not None:
            return None
        values = {a.field: a.value for a in self.answers}
        competitors = values.pop("known_competitors")
        names = (
            ()
            if competitors.casefold() in {"", "无", "没有", "暂无", "none"} | UNKNOWN_PROJECT_VALUES
            else tuple(
                dict.fromkeys(
                    name.strip() for name in re.split(r"[、,，;；\n]", competitors) if name.strip()
                )
            )
        )
        missing_optional = tuple(
            field for field in ("price_range", "special_notes") if not values.get(field)
        )
        for field in ("price_range", "special_notes"):
            values[field] = values.get(field) or None
        readers = ()
        if self.schema_version == 2:
            readers = parse_readers(values["readers_and_priorities"])
            values["readers_and_priorities"] = reader_table(readers)
        uncertainties = tuple(
            k
            for k, v in values.items()
            if isinstance(v, str) and v.casefold() in UNKNOWN_PROJECT_VALUES
        )
        if self.schema_version == 1:
            missing_optional += ("reader_formats",)
        return ProjectContext(
            project_id=self.project_id,
            known_competitors=names,
            readers=readers,
            uncertainties=tuple(
                dict.fromkeys(
                    uncertainties
                    + missing_optional
                    + (
                        ("known_competitors",)
                        if competitors.casefold() in UNKNOWN_PROJECT_VALUES
                        else ()
                    )
                    + reader_uncertainties(readers)
                )
            ),
            **values,
        )


class InterviewStatus(Contract):
    phase: Literal[0] = 0
    project_id: UUID
    status: Literal["interviewing", "ready"]
    question_id: Optional[QuestionID] = None
    question: Optional[Text] = None
    context: Optional[ProjectContext] = None

    @model_validator(mode="after")
    def check_status(self) -> "InterviewStatus":
        if self.status == "ready":
            if self.context is None or self.question_id or self.question:
                raise ValueError("Ready status needs complete context and no pending question")
            if self.context.project_id != self.project_id:
                raise ValueError("Project status and context identities must match")
        elif self.context is not None or not self.question_id or not self.question:
            raise ValueError("Interviewing status needs a pending question and no ready context")
        return self


def interview_status(interview: ProjectInterview) -> InterviewStatus:
    field = interview.next_field
    return InterviewStatus(
        project_id=interview.project_id,
        status="interviewing" if field else "ready",
        question_id=field,
        question=QUESTIONS[field] if field else None,
        context=interview.context,
    )


class ProjectInitializationRequired(ValueError):
    pass


def require_project(context: Optional[ProjectContext]) -> ProjectContext:
    if context is None:
        raise ProjectInitializationRequired("Complete the stage 0 project interview first")
    return ProjectContext.model_validate_json(context.model_dump_json())


class ProjectStore:
    """One project per selected SQLite file. Reads never create a missing project."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def load(self) -> Optional[ProjectInterview]:
        if not self.path.exists():
            return None
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported project schema")
            row = db.execute("SELECT document FROM project WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("Project store is incomplete")
            return ProjectInterview.model_validate_json(row[0])
        finally:
            db.close()

    def start(self) -> ProjectInterview:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path))
        try:
            with db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise ValueError("Unsupported project schema")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS project "
                    "(singleton INTEGER PRIMARY KEY, document TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT OR IGNORE INTO project VALUES (1, ?)",
                    (ProjectInterview().model_dump_json(),),
                )
                db.execute("PRAGMA user_version=1")
        finally:
            db.close()
        interview = self.load()
        if interview is None:
            raise ValueError("Project store was removed during initialization")
        return interview

    def answer(self, field: QuestionID, value: str) -> ProjectInterview:
        if not self.path.exists():
            raise ValueError("Start the interview before answering")
        db = sqlite3.connect(str(self.path))
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Unsupported project schema")
                row = db.execute("SELECT document FROM project WHERE singleton=1").fetchone()
                if row is None:
                    raise ValueError("Project store is incomplete")
                result = ProjectInterview.model_validate_json(row[0]).answer(field, value)
                db.execute(
                    "UPDATE project SET document=? WHERE singleton=1", (result.model_dump_json(),)
                )
            return result
        finally:
            db.close()
