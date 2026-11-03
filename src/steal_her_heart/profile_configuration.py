"""Readable profile.md v2. Business data has one source, not a shadow JSON copy."""

import hashlib
import html
import re
from importlib.resources import files
from typing import Literal, Optional
from uuid import UUID

from pydantic import Field

from .schemas import UNKNOWN_PROJECT_VALUES, Contract, ProjectContext, ReaderProfile

LABELS = {
    "名称与一句话描述": "product",
    "所属行业 / 业态（可多选）": "industry",
    "目标客群": "target_customers",
    "市场范围": "market_scope",
    "核心优势": "strengths",
    "主要短板": "weaknesses",
    "价格区间": "price_range",
}
PLACEHOLDERS = {"待填写", "未填写", "请填写", "（请填写）"}


class ReaderConfigurationIncomplete(ValueError):
    pass


class ProfileIdentity(Contract):
    schema_version: Literal[2] = 2
    project_id: UUID
    reader_ids: dict[str, str] = Field(default_factory=dict)


def blank_profile() -> str:
    return files("steal_her_heart").joinpath("templates/profile.md").read_text(encoding="utf-8")


def display(value: str) -> str:
    return (
        html.escape(value, quote=False)
        .replace("|", "&#124;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def clean(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    lines = [
        line
        for line in value.splitlines()
        if not re.match(r"^\s*[（(](?:例|例如|请填写)[：:].*[）)]\s*$", line)
    ]
    return html.unescape("\n".join(lines).strip())


def missing(value: str) -> bool:
    return not value.strip() or value.strip() in PLACEHOLDERS


def parse_readers(
    value: str, reader_ids: Optional[dict[str, str]] = None
) -> tuple[ReaderProfile, ...]:
    """Accept a three-column Markdown table or label | concern | format entries."""
    ids = reader_ids or {}
    readers = []
    labels = set()
    lines = value.splitlines() if "\n" in value else re.split(r"[；;]", value)
    for line in lines:
        line = line.strip()
        if not line or line.startswith("<!--"):
            continue
        cells = [
            clean(cell.replace(r"\|", "|")) for cell in re.split(r"(?<!\\)\|", line.strip("|"))
        ]
        if len(cells) == 3 and (
            cells == ["读者", "最关心什么", "期望格式"]
            or all(re.fullmatch(r":?-+:?", cell) for cell in cells)
        ):
            continue
        if len(cells) == 3 and not any(cells):
            continue
        if len(cells) < 3 or (len(cells) == 3 and any(missing(cell) for cell in cells)):
            raise ReaderConfigurationIncomplete(
                "Each reader needs a role, concern and expected format"
            )
        if len(cells) != 3:
            raise ValueError("Reader rows must contain exactly three columns")
        label, concern, output = cells
        if (
            label.casefold() in UNKNOWN_PROJECT_VALUES
            or concern.casefold() in UNKNOWN_PROJECT_VALUES
        ):
            raise ReaderConfigurationIncomplete("The reader role and concern need clarification")
        if label in labels:
            raise ValueError("Reader labels must be unique")
        labels.add(label)
        reader_id = ids.get(label) or "reader-" + hashlib.sha256(label.encode()).hexdigest()[:16]
        priorities = tuple(part.strip() for part in re.split(r"[；;]", concern) if part.strip())
        if all(missing(p) or p.casefold() in UNKNOWN_PROJECT_VALUES for p in priorities):
            raise ReaderConfigurationIncomplete("The reader concern needs clarification")
        readers.append(
            ReaderProfile(id=reader_id, label=label, priorities=priorities, output_style=output)
        )
    if len({reader.id for reader in readers}) != len(readers):
        raise ValueError("Reader IDs must be unique")
    return tuple(readers)


def reader_uncertainties(readers: tuple[ReaderProfile, ...]) -> tuple[str, ...]:
    gaps = []
    if any(r.output_style.casefold() in UNKNOWN_PROJECT_VALUES for r in readers):
        gaps.append("reader_formats")
    if any(
        missing(priority) or priority.casefold() in UNKNOWN_PROJECT_VALUES
        for reader in readers
        for priority in reader.priorities
    ):
        gaps.append("reader_priorities")
    return tuple(gaps)


def reader_table(readers: tuple[ReaderProfile, ...]) -> str:
    rows = ["| 读者 | 最关心什么 | 期望格式 |", "|---|---|---|"]
    rows += [
        f"| {display(r.label)} | {display('；'.join(r.priorities))} | {display(r.output_style)} |"
        for r in readers
    ]
    return "\n".join(rows)


def render_profile(context: Optional[ProjectContext]) -> str:
    text = blank_profile()
    if context is None:
        return text
    readers = context.readers or parse_readers(context.readers_and_priorities)
    for label, field in LABELS.items():
        text = re.sub(
            rf"^- {re.escape(label)}：$",
            lambda match: match[0] + display(getattr(context, field) or ""),
            text,
            count=1,
            flags=re.M,
        )
    text = text.replace("<!-- objective -->", display(context.objective))
    text = text.replace(
        "| | | |",
        "\n".join(
            f"| {display(r.label)} | {display('；'.join(r.priorities))} | "
            f"{display(r.output_style)} |"
            for r in readers
        ),
    )
    text = text.replace(
        "<!-- competitors -->",
        "\n".join(f"- {display(name)}" for name in context.known_competitors)
        or ("待确认" if "known_competitors" in context.uncertainties else ""),
    )
    text = text.replace("<!-- special_notes -->", display(context.special_notes or ""))
    identity = ProfileIdentity(
        project_id=context.project_id, reader_ids={reader.label: reader.id for reader in readers}
    )
    metadata = identity.model_dump_json().replace("-->", "--\\u003e")
    return text.replace("# 项目配置", "# 项目配置\n\n<!-- profile-meta: " + metadata + " -->", 1)


def parse_profile(text: str, fallback_project_id: UUID) -> Optional[ProjectContext]:
    """A partial or blank standard template stays uninitialized; ambiguity fails closed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "# 项目配置":
        raise ValueError("Expected the standard project configuration heading")
    metadata = re.findall(r"^<!-- profile-meta: (.+) -->$", text, re.M)
    if len(metadata) > 1:
        raise ValueError("Multiple profile identities are not allowed")
    identity = ProfileIdentity.model_validate_json(metadata[0]) if metadata else None
    if metadata:
        first_section = text.find("\n## ")
        if text.index("<!-- profile-meta:") > first_section:
            raise ValueError("Profile identity must be in the header")
    expected = {"我们的产品", "分析目的", "读者与关注点", "已知竞品", "特别说明"}
    sections: dict[str, list[str]] = {}
    current = None
    fence = None
    for line in lines:
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            if current:
                sections[current].append(line)
            continue
        heading = line[3:].strip() if line.startswith("## ") and fence is None else None
        if heading == "已知竞品（可为空）":
            heading = "已知竞品"
        if heading in expected:
            if heading in sections:
                raise ValueError("Duplicate profile sections")
            current = heading
            sections[current] = []
        elif current:
            sections[current].append(line)
    if not expected <= set(sections):
        raise ValueError("Missing standard profile sections")
    field_lines: dict[str, list[str]] = {}
    current_field = None
    for line in sections["我们的产品"]:
        match = re.match(r"^-\s*(.+?)[：:](.*)$", line.strip())
        if match and match[1] in LABELS:
            field = LABELS[match[1]]
            if field in field_lines:
                raise ValueError("Duplicate product fields")
            current_field = field
            field_lines[field] = [match[2]]
        elif current_field:
            field_lines[current_field].append(line)
        elif clean(line):
            raise ValueError("Product text must belong to a standard field")
    if len(field_lines) != len(LABELS):
        raise ValueError("Missing standard product fields")
    fields = {field: clean("\n".join(lines)) for field, lines in field_lines.items()}
    objective = clean("\n".join(sections["分析目的"]))
    core = ("product", "industry", "target_customers", "market_scope", "strengths", "weaknesses")
    if any(missing(fields[field]) for field in core) or missing(objective):
        return None
    if any(
        fields[field].casefold() in UNKNOWN_PROJECT_VALUES
        for field in ("product", "industry", "target_customers", "market_scope")
    ) or (objective.casefold() in UNKNOWN_PROJECT_VALUES):
        return None
    try:
        readers = parse_readers(
            "\n".join(sections["读者与关注点"]), identity.reader_ids if identity else None
        )
    except ReaderConfigurationIncomplete:
        return None
    if not readers:
        return None
    competitors = []
    competitors_unknown = False
    for line in sections["已知竞品"]:
        value = clean(re.sub(r"^\s*-\s*", "", line))
        if value.casefold() in UNKNOWN_PROJECT_VALUES:
            competitors_unknown = True
            continue
        if value.casefold() not in {"", "无", "没有", "暂无", "none"}:
            if re.match(r"^\s*-\s+", line):
                competitors.append(value)
            else:
                competitors.extend(
                    clean(part) for part in re.split(r"[、，,;；]", value) if clean(part)
                )
    notes = clean("\n".join(sections["特别说明"])) or None
    fields["price_range"] = None if missing(fields["price_range"]) else fields["price_range"]
    uncertainties = [
        field
        for field, value in fields.items()
        if value is None or value.casefold() in UNKNOWN_PROJECT_VALUES
    ]
    if notes is None or notes.casefold() in UNKNOWN_PROJECT_VALUES:
        uncertainties.append("special_notes")
    if competitors_unknown:
        uncertainties.append("known_competitors")
    uncertainties.extend(reader_uncertainties(readers))
    return ProjectContext(
        project_id=identity.project_id if identity else fallback_project_id,
        objective=objective,
        readers=readers,
        readers_and_priorities=reader_table(readers),
        known_competitors=tuple(dict.fromkeys(competitors)),
        special_notes=notes,
        uncertainties=tuple(uncertainties),
        **fields,
    )
