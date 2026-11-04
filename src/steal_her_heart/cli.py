"""CLI for foundation diagnostics and resumable task creation."""

import argparse
import asyncio
import os
import sqlite3
import stat
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from .brief_recognition import BriefRecognitionTool
from .command_router import RequestRoutingTool
from .execution_readiness import (
    PATHS_NOTICE,
    PREVIEW_NOTICE,
    profile_saved_guidance,
    render_guidance,
)
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
from .schemas import ExecutionGuidance, ToolContext, ToolError, ToolResult
from .tools import HelloTool, ToolRegistry


class _PreviewParser(argparse.ArgumentParser):
    """Keep capability disclosure visible in the root and every subcommand help."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("description", PREVIEW_NOTICE)
        kwargs.setdefault("epilog", PATHS_NOTICE)
        super().__init__(*args, **kwargs)


def _project_status_result(status: InterviewStatus) -> int:
    print(status.model_dump_json(indent=2))
    if status.status == "ready":
        print(render_guidance(profile_saved_guidance()), file=sys.stderr)
        return 0
    return 3


def _preset(args) -> int:
    """Optional configuration/metadata diagnostic, with no research side effects."""
    from .review_sampling import ResearchPresetTool, parse_review_sample_json

    arguments = {"name": args.name}
    try:
        if (args.review_sample is None) != (args.as_of is None):
            raise ValueError("Sample and date must be supplied together")
        if args.review_sample is not None:
            descriptor = os.open(args.review_sample, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                    raise ValueError("Sampling input must be a bounded regular JSON file")
                payload = stream.read(1048577)
            if len(payload) > 1048576:
                raise ValueError("Sampling input is too large")
            batch = parse_review_sample_json(payload)
            arguments.update(review_sample=batch.model_dump(), as_of=args.as_of)
    except (ValueError, OSError):
        result = ToolResult(
            tool="research_preset",
            status="error",
            error=ToolError(
                code="PRESET_INPUT_INVALID",
                message="Provide a valid bounded sample JSON and explicit --as-of together.",
            ),
        )
        print(result.model_dump_json(indent=2))
        return 2
    registry = ToolRegistry()
    registry.register(ResearchPresetTool())
    result = asyncio.run(
        registry.invoke(
            "research_preset",
            arguments,
            ToolContext(
                task_id=uuid4(), capabilities=frozenset({"research_configuration"}), timeout_s=5
            ),
        )
    )
    print(result.model_dump_json(indent=2))
    if result.status == "error":
        return 2 if result.error.code == "INVALID_ARGUMENT" else 1
    assessment = result.data["review_assessment"]
    return 3 if assessment is not None and not assessment["coverage_complete"] else 0


def _search(args) -> int:
    """Explicit network diagnostic; search hits are discovery leads, not evidence."""
    from .search_ledger import SearchLedger
    from .web_search import WebSearchTool

    task_id = args.task_id or uuid4()
    request_id = args.request_id or uuid4()
    arguments = {
        "query": args.query,
        "max_results": args.max_results,
        "request_id": request_id,
    }
    try:
        registry = ToolRegistry()
        registry.register(WebSearchTool(ledger=SearchLedger(args.search_ledger)))
        result = asyncio.run(
            registry.invoke(
                "web_search",
                arguments,
                ToolContext(
                    task_id=task_id,
                    capabilities=frozenset({"web_search"}),
                    timeout_s=15,
                ),
            )
        )
    except (ValueError, OSError, sqlite3.Error):
        result = ToolResult(
            tool="web_search",
            status="error",
            error=ToolError(
                code="SEARCH_LOCAL_ERROR", message="Search ledger could not be initialized"
            ),
        )
    print(f"search_task_id={task_id}", file=sys.stderr)
    print(f"search_request_id={request_id}", file=sys.stderr)
    print(result.model_dump_json(indent=2))
    if result.status == "ok":
        return 0
    return 2 if result.error.code == "INVALID_ARGUMENT" else 1


def _fetch(args) -> int:
    """Acquire a static page independently; a stored Source is not validated Evidence."""
    from .source_snapshots import SnapshotStore
    from .web_fetch import FetchLedger, WebFetchTool

    task_id = args.task_id or uuid4()
    request_id = args.request_id or uuid4()
    try:
        registry = ToolRegistry()
        registry.register(
            WebFetchTool(
                ledger=FetchLedger(args.fetch_ledger),
                snapshots=SnapshotStore(args.sources_root),
            )
        )
        result = asyncio.run(
            registry.invoke(
                "web_fetch",
                {"url": args.url, "request_id": request_id},
                ToolContext(
                    task_id=task_id,
                    capabilities=frozenset({"web_fetch"}),
                    timeout_s=15,
                ),
            )
        )
    except (ValueError, OSError, sqlite3.Error):
        result = ToolResult(
            tool="web_fetch",
            status="error",
            error=ToolError(
                code="FETCH_LOCAL_ERROR",
                message="Page acquisition storage could not be initialized",
            ),
        )
    print(f"fetch_task_id={task_id}", file=sys.stderr)
    print(f"fetch_request_id={request_id}", file=sys.stderr)
    print(result.model_dump_json(indent=2))
    if result.status == "ok":
        return 0
    return 2 if result.error.code == "INVALID_ARGUMENT" else 1


def _text_span(value: str) -> dict[str, int]:
    parts = value.split(":")
    if len(parts) != 2 or any(not part.isascii() or not part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError("A span must be START:END using non-negative integers")
    return {"start": int(parts[0]), "end": int(parts[1])}


def _extract_candidates(args) -> int:
    """Bind explicit spans locally; this does not validate or reuse research assets."""
    from .evidence_extraction import EvidenceExtractionTool
    from .source_snapshots import SnapshotStore

    task_id = uuid4()
    try:
        registry = ToolRegistry()
        registry.register(EvidenceExtractionTool(snapshots=SnapshotStore(args.sources_root)))
        result = asyncio.run(
            registry.invoke(
                "extract_features",
                {
                    "snapshot_ref": args.snapshot_ref,
                    "text_sha256": args.text_sha256,
                    "competitor": args.competitor,
                    "dimension": args.dimension,
                    "spans": args.span,
                },
                ToolContext(
                    task_id=task_id,
                    capabilities=frozenset({"evidence_extraction"}),
                    timeout_s=5,
                ),
            )
        )
    except (ValueError, OSError):
        result = ToolResult(
            tool="extract_features",
            status="error",
            error=ToolError(
                code="EXTRACT_LOCAL_ERROR",
                message="Local source snapshot could not be initialized",
            ),
        )
    print(f"extraction_task_id={task_id}", file=sys.stderr)
    print(result.model_dump_json(indent=2))
    if result.status == "ok":
        return 0
    return 2 if result.error.code == "INVALID_ARGUMENT" else 1


def _review_project(args) -> int:
    """Local terminal UI only: never register approval as a model-callable tool."""
    from .project_review import parse_project_review_json, run_project_review

    def failure(code, message, exit_code):
        result = ToolResult(
            tool="project_review",
            status="error",
            error=ToolError(code=code, message=message),
        )
        print(result.model_dump_json(indent=2))
        return exit_code

    if args.project_name is None or args.project is not None:
        return failure(
            "REVIEW_PROJECT_REQUIRED",
            "Choose an existing project with --project-name; legacy --project is not supported.",
            2,
        )
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return failure(
            "REVIEW_INTERACTIVE_REQUIRED",
            "Run this local review in an interactive terminal. "
            "Piped or automatic approval is disabled.",
            2,
        )
    try:
        descriptor = os.open(
            args.review_file,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                raise ValueError("A review requires a bounded regular JSON file")
            payload = stream.read(1048577)
        request = parse_project_review_json(payload)
        workspace = ProjectWorkspace(args.projects_root, args.project_name)
    except (ValueError, OSError):
        return failure(
            "REVIEW_INPUT_INVALID",
            "Provide a valid project and bounded proposal JSON; approval fields are not accepted.",
            2,
        )

    def confirm(question):
        print(question.markdown, file=sys.stderr)
        print(f"\n请逐字输入：{question.token}", file=sys.stderr)
        print("其他输入、EOF 或中断均表示不批准。", file=sys.stderr)
        with redirect_stdout(sys.stderr):
            return input("> ")

    try:
        result = run_project_review(workspace, request, confirm)
    except (ValueError, OSError):
        return failure(
            "REVIEW_LOCAL_ERROR",
            "The review could not be persisted or verified; "
            "scope is not confirmed by this response.",
            1,
        )
    print(result.model_dump_json(indent=2))
    return {"locked": 0, "rejected": 3, "failed": 1}[result.status]


def main(argv=None) -> int:
    parser = _PreviewParser(prog="steal-her-heart")
    parser.add_argument("--db", type=Path, help="Override the task database path")
    parser.add_argument("--project", type=Path, help="Legacy SQLite interview path")
    parser.add_argument("--projects-root", type=Path, default=Path("projects"))
    parser.add_argument("--project-name", help="Optionally bind to an existing product project")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hello", help="Offline tool/schema diagnostic; performs no research")
    review = sub.add_parser(
        "review-project",
        help="Interactively confirm a supplied project scope and final dimensions; no research",
    )
    review.add_argument("--review-file", type=Path, required=True)
    preset = sub.add_parser(
        "preset", help="Load an optional research recipe; does not start research"
    )
    preset.add_argument("name", choices=["saas"])
    preset.add_argument(
        "--review-sample",
        type=Path,
        help="Declared review records and collection summaries as JSON",
    )
    preset.add_argument(
        "--as-of", type=date.fromisoformat, help="Explicit YYYY-MM-DD for sample coverage checks"
    )
    search = sub.add_parser(
        "search", help="Query public search once; returns leads, not a completed analysis"
    )
    search.add_argument("query")
    search.add_argument("--max-results", type=int, default=5)
    search.add_argument("--task-id", type=UUID, help="Reuse a task's persistent search budget")
    search.add_argument("--request-id", type=UUID, help="Replay the same recorded search call")
    search.add_argument(
        "--search-ledger", type=Path, default=Path(".steal-her-heart/search-calls.sqlite3")
    )
    fetch = sub.add_parser(
        "fetch",
        help="Acquire one public static page and save a source snapshot, not validated facts",
    )
    fetch.add_argument("url")
    fetch.add_argument("--task-id", type=UUID, help="Reuse a task's persistent acquisition budget")
    fetch.add_argument("--request-id", type=UUID, help="Replay the same recorded acquisition")
    fetch.add_argument(
        "--fetch-ledger", type=Path, default=Path(".steal-her-heart/fetch-calls.sqlite3")
    )
    fetch.add_argument("--sources-root", type=Path, default=Path(".steal-her-heart/sources"))
    extract = sub.add_parser(
        "extract-candidates",
        help="Bind explicit local source spans to unvalidated candidates, without a model",
    )
    extract.add_argument("snapshot_ref")
    extract.add_argument("--text-sha256", required=True)
    extract.add_argument("--competitor", required=True)
    extract.add_argument("--dimension", required=True)
    extract.add_argument("--span", type=_text_span, action="append", required=True)
    extract.add_argument("--sources-root", type=Path, default=Path(".steal-her-heart/sources"))
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
    if args.command == "review-project":
        return _review_project(args)
    if args.command == "preset":
        return _preset(args)
    if args.command == "search":
        return _search(args)
    if args.command == "fetch":
        return _fetch(args)
    if args.command == "extract-candidates":
        return _extract_candidates(args)
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
                    return _project_status_result(
                        InterviewStatus(
                            project_id=profile.project_id, status="ready", context=profile
                        )
                    )
                workspace.require_interview_template()
            if args.command in {"init-project", "/init-project"}:
                if workspace:
                    workspace.initialize_documents(ProjectInterview())
                    existing = workspace.read_required().context
                    if existing is not None:
                        return _project_status_result(
                            InterviewStatus(
                                project_id=existing.project_id, status="ready", context=existing
                            )
                        )
                    workspace.require_interview_template()
                interview = project_store.start()
                if args.interactive:
                    while interview.next_field:
                        field = interview.next_field
                        try:
                            with redirect_stdout(sys.stderr):
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
        return _project_status_result(status)
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
        if result.data.get("guidance") is not None:
            print(
                render_guidance(ExecutionGuidance.model_validate(result.data["guidance"])),
                file=sys.stderr,
            )
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
    if result.guidance is not None:
        print(render_guidance(result.guidance), file=sys.stderr)
    if args.command == "status":
        return 0
    return {"completed": 0, "failed": 1, "pending_module": 3, "waiting_confirmation": 3}[
        result.outcome
    ]


if __name__ == "__main__":
    raise SystemExit(main())
