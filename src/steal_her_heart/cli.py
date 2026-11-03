"""CLI for foundation diagnostics and resumable task creation."""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path
from uuid import UUID, uuid4

from .brief_recognition import BriefRecognitionTool
from .command_router import RequestRoutingTool
from .memory import ConcurrentUpdate, MemoryStore
from .orchestrator import Orchestrator
from .project_initialization import (
    QUESTIONS,
    InterviewStatus,
    ProjectInterview,
    ProjectStore,
    interview_status,
)
from .project_workspace import ManualProfileIncomplete, ProjectWorkspace
from .schemas import ToolContext
from .tools import HelloTool, ToolRegistry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="steal-her-heart")
    parser.add_argument("--db", type=Path, help="Override the task database path")
    parser.add_argument("--project", type=Path, help="Legacy SQLite interview path")
    parser.add_argument("--projects-root", type=Path, default=Path("projects"))
    parser.add_argument("--project-name", help="Optionally bind to an existing product project")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hello", help="Offline tool/schema diagnostic; performs no research")
    initialize = sub.add_parser(
        "init-project",
        aliases=["/init-project"],
        help="Create/resume an independent product project",
    )
    initialize.add_argument("--interactive", action="store_true")
    answer = sub.add_parser(
        "answer-project", help="Answer the currently pending interview question"
    )
    answer.add_argument("question_id", choices=list(QUESTIONS))
    answer.add_argument("answer", nargs="?", default="")
    sub.add_parser("project-status", help="Read the saved project interview")
    run = sub.add_parser("run", help="Create a task; phase 1 stops before Planner")
    run.add_argument("user_input")
    brief = sub.add_parser(
        "recognize-brief",
        aliases=["brief"],
        help="Recognize a ResearchBrief offline (legacy alias: brief; not /brief reporting)",
    )
    brief.add_argument("user_input")
    request = sub.add_parser("request", help="Route a slash command or natural-language request")
    request.add_argument("user_input")
    for command in ("analyze", "battlecard"):
        action = sub.add_parser(
            command, aliases=[f"/{command}"], help="Route a single-competitor task"
        )
        action.add_argument("target", nargs="*")
    report = sub.add_parser("/brief", help="Route a periodic report for configured project readers")
    report.add_argument("period", nargs="?", default="")
    for command in ("status", "resume"):
        child = sub.add_parser(command)
        child.add_argument("task_id", type=UUID)
    args = parser.parse_args(argv)
    project_commands = {"init-project", "/init-project", "answer-project", "project-status"}
    try:
        workspace = None
        if args.project is None and (
            args.project_name is not None or args.command in project_commands
        ):
            name = args.project_name if args.project_name is not None else "default"
            workspace = ProjectWorkspace(args.projects_root, name)
        project_store = (
            ProjectStore(args.project)
            if args.project is not None
            else (ProjectStore(workspace.interview_path) if workspace else None)
        )
        if args.db is None:
            args.db = (
                workspace.path / ".state" / "memory.sqlite3"
                if workspace
                else Path(".steal-her-heart/memory.sqlite3")
            )
    except ValueError:
        print("项目标识无效或目录越界。", file=sys.stderr)
        return 2
    if args.command in project_commands:
        try:
            if workspace and args.command == "project-status":
                profile = workspace.read_required().context
                if profile is not None:
                    print(
                        InterviewStatus(
                            project_id=profile.project_id, status="ready", context=profile
                        ).model_dump_json(indent=2)
                    )
                    return 0
                workspace.require_interview_template()
            if args.command in {"init-project", "/init-project"}:
                if workspace:
                    workspace.initialize_documents(ProjectInterview())
                    existing = workspace.read_required().context
                    if existing is not None:
                        print(
                            InterviewStatus(
                                project_id=existing.project_id, status="ready", context=existing
                            ).model_dump_json(indent=2)
                        )
                        return 0
                    workspace.require_interview_template()
                interview = project_store.start()
                if args.interactive:
                    while interview.next_field:
                        field = interview.next_field
                        try:
                            value = input(QUESTIONS[field] + "\n> ")
                        except (EOFError, KeyboardInterrupt):
                            print("\n访谈进度已保存，稍后可继续。", file=sys.stderr)
                            return 3
                        try:
                            interview = project_store.answer(field, value)
                        except ValueError:
                            print("此项不能留空；可明确说明待确认项。", file=sys.stderr)
            elif args.command == "answer-project":
                if workspace:
                    workspace.require_interview_template()
                interview = project_store.answer(args.question_id, args.answer)
            else:
                interview = project_store.load()
                if interview is None:
                    raise ValueError("Project interview has not started")
            if workspace:
                workspace.initialize_documents(interview)
            status = interview_status(interview)
            if workspace and status.status == "ready":
                profile = workspace.read_required().context
                if profile is None:
                    raise ValueError("Project profile has not been completed")
                status = InterviewStatus(
                    project_id=profile.project_id, status="ready", context=profile
                )
        except ManualProfileIncomplete as error:
            print(str(error), file=sys.stderr)
            return 3
        except (ValueError, OSError, sqlite3.Error):
            print(
                "项目访谈文件、回答或问题顺序无效；请先 init-project 并按当前问题作答。",
                file=sys.stderr,
            )
            return 2
        print(status.model_dump_json(indent=2))
        return 0 if status.status == "ready" else 3
    interview = None
    if project_store is not None and args.command in {
        "request",
        "analyze",
        "/analyze",
        "battlecard",
        "/battlecard",
        "/brief",
        "run",
        "resume",
    }:
        try:
            interview = project_store.load()
        except (ValueError, OSError, sqlite3.Error):
            print("项目上下文无法读取；未启动研究。", file=sys.stderr)
            return 2
    project = interview.context if interview else None
    workspace_question = None
    if workspace and args.command in {
        "request",
        "analyze",
        "/analyze",
        "battlecard",
        "/battlecard",
        "/brief",
        "run",
        "resume",
    }:
        try:
            project = workspace.read_required().context
            if project is None:
                workspace.require_interview_template()
        except ManualProfileIncomplete as error:
            project = None
            workspace_question = str(error)
        except FileNotFoundError:
            project = None
            workspace_question = "项目缺少 profile.md 或 dimensions.md，请先执行 /init-project。"
        except (ValueError, OSError):
            print("项目 profile.md 或 dimensions.md 无效；未启动研究。", file=sys.stderr)
            return 2
    if project_store is not None and project is None and workspace_question is None:
        workspace_question = (
            QUESTIONS[interview.next_field]
            if interview and interview.next_field
            else QUESTIONS["product"]
        )
    if args.command in {"run", "resume"} and project_store is not None and project is None:
        waiting = interview if interview and interview.context is None else ProjectInterview()
        status = interview_status(waiting)
        if workspace_question:
            values = status.model_dump()
            values["question"] = workspace_question
            status = InterviewStatus.model_validate(values)
        print(status.model_dump_json(indent=2))
        return 3
    registry = ToolRegistry()
    registry.register(HelloTool())
    if args.command in {"request", "analyze", "/analyze", "battlecard", "/battlecard", "/brief"}:
        raw = (
            args.user_input
            if args.command == "request"
            else (
                f"/brief {args.period}"
                if args.command == "/brief"
                else f"/{args.command.lstrip('/')} {' '.join(args.target)}"
            )
        )
        registry.register(
            RequestRoutingTool(
                project,
                workspace_question,
            )
        )
        result = asyncio.run(
            registry.invoke(
                "route_request",
                {"user_input": raw},
                ToolContext(task_id=uuid4(), capabilities=frozenset({"request_routing"})),
            )
        )
        print(result.model_dump_json(indent=2))
        if result.status == "error":
            return 2 if result.error.code == "INVALID_ARGUMENT" else 1
        return 2 if result.data["execution"] == "unsupported" else 3
    if args.command in {"brief", "recognize-brief"}:
        registry.register(BriefRecognitionTool())
        result = asyncio.run(
            registry.invoke(
                "recognize_brief",
                {"user_input": args.user_input},
                ToolContext(task_id=uuid4(), capabilities=frozenset({"brief_recognition"})),
            )
        )
        print(result.model_dump_json(indent=2))
        if result.status == "error":
            return 2 if result.error.code == "INVALID_ARGUMENT" else 1
        return 0 if result.data["status"] == "ready" else 3
    if args.command == "hello":
        result = asyncio.run(
            registry.invoke(
                "hello", {}, ToolContext(task_id=uuid4(), capabilities=frozenset({"diagnostic"}))
            )
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "ok" else 1
    try:
        agent = Orchestrator(MemoryStore(args.db), registry, project)
        if args.command == "run":
            result = agent.run_task(args.user_input)
        elif args.command == "resume":
            result = agent.resume(args.task_id)
        else:
            result = agent.status(args.task_id)
    except (ValueError, KeyError):
        print("Invalid input, schema, or unknown task ID", file=sys.stderr)
        return 2
    except ConcurrentUpdate:
        print("Task changed in another process; reload before resuming", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("Local memory could not be accessed", file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2))
    if args.command == "status":
        return 0
    return {"completed": 0, "failed": 1, "pending_module": 3, "waiting_confirmation": 3}[
        result.outcome
    ]


if __name__ == "__main__":
    raise SystemExit(main())
