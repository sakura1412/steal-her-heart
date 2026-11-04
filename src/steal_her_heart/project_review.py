"""Local project review bridge; no research execution or chat authentication.

Only trusted application code may supply ``confirmer``. It must display the
question and obtain a fresh human response (the CLI requires a local terminal).
This callback is not an identity provider, and these functions are not model
tools. The files below are an audit trail, not a cross-file transaction or an
automatic recovery protocol. Reuse still requires the existing lock guards.
"""

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Literal, Optional
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .project_initialization import require_project
from .project_workspace import DRAFT_DIMENSIONS, ProjectWorkspace
from .research_workflow import (
    CollectionJob,
    CompetitorApproval,
    DimensionLibrary,
    DimensionLock,
    DimensionProposal,
    DiscoveryProposal,
    confirm_competitors,
    confirm_dimensions,
    design_dimensions,
    digest,
    prepare_collection_jobs,
    render_dimensions,
    write_dimensions,
)
from .schemas import Contract, Identifier, ProjectContext, ReaderProfile, Text

MAX_INPUT_BYTES = 1048576
MAX_RECORD_BYTES = 16777216


class ProjectReviewRequest(Contract):
    discovery: DiscoveryProposal
    selected_ids: tuple[Identifier, ...] = Field(min_length=1)
    library: DimensionLibrary
    core_field_ids: Optional[tuple[Identifier, ...]] = None
    extension_ids: tuple[Identifier, ...] = ()
    selection_rationale: Text
    readers: tuple[ReaderProfile, ...] = ()

    @model_validator(mode="after")
    def check_selection(self) -> "ProjectReviewRequest":
        if len(set(self.selected_ids)) != len(self.selected_ids) or not set(self.selected_ids) <= {
            c.id for c in self.discovery.candidates
        }:
            raise ValueError("Choose unique candidates from the supplied discovery")
        if len(set(self.extension_ids)) != len(self.extension_ids) or not set(
            self.extension_ids
        ) <= {f.id for f in self.library.extensions}:
            raise ValueError("Choose unique extensions from the supplied library")
        return self


class ReviewQuestion(Contract):
    stage: Literal["competitors", "dimensions"]
    token: Text
    markdown: Annotated[str, Field(strict=True, min_length=1, max_length=MAX_RECORD_BYTES)]


class ProjectReviewResult(Contract):
    status: Literal["locked", "rejected", "failed"]
    review_id: UUID
    research_ready: Literal[False] = False
    scope_locked: bool
    jobs: tuple[CollectionJob, ...] = ()
    record_path: str
    error_code: Optional[str] = None

    @model_validator(mode="after")
    def check_outcome(self) -> "ProjectReviewResult":
        if self.status == "locked":
            if not self.scope_locked or not self.jobs or self.error_code is not None:
                raise ValueError("Locked results need verified jobs and no error")
        elif self.scope_locked or self.jobs:
            raise ValueError("Unsuccessful reviews cannot deliver a locked scope")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("Only failed results carry an error code")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _parse(payload: bytes, limit: int):
    if not isinstance(payload, bytes) or len(payload) > limit:
        raise ValueError("Expected bounded JSON bytes")
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Invalid constant")),
        )
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid JSON") from error


def parse_project_review_json(payload: bytes) -> ProjectReviewRequest:
    """Read an explicit configuration; no consent flags are accepted in its schema."""
    return ProjectReviewRequest.model_validate(_parse(payload, MAX_INPUT_BYTES))


class _ReviewStop(Exception):
    pass


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_directory(path: Path) -> None:
    for component in (path, *path.parents):
        if not stat.S_ISDIR(component.lstat().st_mode):
            raise _ReviewStop("REVIEW_UNSAFE_PATH")


def _mkdir(path: Path) -> None:
    _safe_directory(path.parent)
    path.mkdir(exist_ok=True)
    _safe_directory(path)


def _safe_file(path: Path, *, optional=False) -> None:
    _safe_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _ReviewStop("REVIEW_UNSAFE_PATH")


def _read(path: Path) -> bytes:
    _safe_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECORD_BYTES:
            raise _ReviewStop("REVIEW_UNSAFE_PATH")
        payload = stream.read(MAX_RECORD_BYTES + 1)
    if len(payload) > MAX_RECORD_BYTES:
        raise _ReviewStop("REVIEW_UNSAFE_PATH")
    return payload


def _load(path: Path, model):
    return model.model_validate(_parse(_read(path), MAX_RECORD_BYTES))


def _write(path: Path, value: Contract) -> None:
    """Publish a complete audit record once; never follow or replace a target."""
    _safe_directory(path.parent)
    payload = (value.model_dump_json(indent=2) + "\n").encode()
    if len(payload) > MAX_RECORD_BYTES:
        raise _ReviewStop("REVIEW_RECORD_TOO_LARGE")
    temporary = path.with_name(".pending-" + str(uuid4()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.link(temporary, path, follow_symlinks=False)
        published = True
        temporary.unlink()
    except BaseException:
        # Only remove a target that this call actually published. In particular,
        # FileExistsError must preserve the earlier record owned by another call.
        if published:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@contextmanager
def _mutex(workspace: ProjectWorkspace):
    """Cooperating local POSIX processes only; stale files do not retain a lock."""
    try:
        import fcntl
    except ImportError as error:
        raise _ReviewStop("REVIEW_PLATFORM_UNAVAILABLE") from error

    _safe_directory(workspace.path)
    state = workspace.path / ".state"
    _mkdir(state)
    lock_path = state / "review-mutex"
    _safe_file(lock_path, optional=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _ReviewStop("REVIEW_UNSAFE_PATH")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _ReviewStop("REVIEW_BUSY") from error
        yield
    finally:
        os.close(fd)


def _current(workspace: ProjectWorkspace) -> ProjectContext:
    for name in ("profile.md", "dimensions.md"):
        _safe_file(workspace.path / name)
    return require_project(workspace.read_required().context)


def _unchanged(workspace, project, dimensions_hash, profile_hash):
    current = _current(workspace)
    if digest(current) != digest(project):
        raise _ReviewStop("REVIEW_SCOPE_CHANGED")
    if _sha(_read(workspace.path / "profile.md")) != profile_hash:
        raise _ReviewStop("REVIEW_SCOPE_CHANGED")
    if _sha(_read(workspace.path / "dimensions.md")) != dimensions_hash:
        raise _ReviewStop("REVIEW_ARTIFACT_CHANGED")
    return current


def _display(value: str, *, newlines=False) -> str:
    """Escape terminal controls, bidi/format controls and user-supplied newlines."""
    return "".join(
        char
        if char.isprintable() or (newlines and char == "\n")
        else "\\u" + format(ord(char), "04x")
        for char in value
    )


def _question(review_id, stage, project, request, markdown, extra):
    payload = {
        "review_id": str(review_id),
        "stage": stage,
        "project": digest(project),
        "request": digest(request),
        **extra,
    }
    fingerprint = _sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return ReviewQuestion(
        stage=stage,
        token=f"confirm {review_id} {fingerprint}",
        markdown=_display(markdown, newlines=True),
    )


class _Decision(Contract):
    stage: Literal["competitors", "dimensions"]
    question_digest: str
    reply_digest: Optional[str]
    accepted: bool
    received_at: datetime
    channel: Literal["trusted_application_callback"] = "trusted_application_callback"


def _ask(folder, question, confirmer):
    _write(folder / f"{question.stage}-question.json", question)
    try:
        reply = confirmer(question)
    except (EOFError, KeyboardInterrupt):
        reply = None
    accepted = isinstance(reply, str) and reply == question.token
    decision = _Decision(
        stage=question.stage,
        question_digest=digest(question),
        reply_digest=_sha(reply.encode()) if isinstance(reply, str) else None,
        accepted=accepted,
        received_at=datetime.now(timezone.utc),
    )
    _write(folder / f"{question.stage}-decision.json", decision)
    if (
        _load(folder / f"{question.stage}-question.json", ReviewQuestion) != question
        or _load(folder / f"{question.stage}-decision.json", _Decision) != decision
    ):
        raise _ReviewStop("REVIEW_RECORD_CHANGED")
    return decision


def _candidate_markdown(request):
    lines = [
        "# 候选名单评审（输入资料不代表已核验事实）",
        "",
        "拟选 ID：" + ", ".join(_display(cid) for cid in request.selected_ids),
    ]
    for candidate in request.discovery.candidates:
        lines += [
            f"- {_display(candidate.id)} | {_display(candidate.name)} | {candidate.tier}",
            f"  依据类型：{candidate.basis}；理由：{_display(candidate.rationale)}",
        ]
        lines += [
            f"  来源：{_display(str(s.url))}；获取：{s.fetched_at.isoformat()}"
            for s in candidate.sources
        ]
    lines += [
        f"- 分类缺口 {gap.tier}：{_display(gap.reason)}" for gap in request.discovery.tier_gaps
    ]
    lines += ["", "替代路径评估：" + _display(request.discovery.alternative_assessment)]
    return "\n".join(lines)


def run_project_review(
    workspace: ProjectWorkspace,
    request: ProjectReviewRequest,
    confirmer: Callable[[ReviewQuestion], str],
) -> ProjectReviewResult:
    """Run once via a trusted UI, returning jobs only after persisted-lock readback.

    The callback boundary is a trusted application assumption, not authentication.
    Interrupted reviews are not resumed or approved automatically. Failed writes
    may leave review artifacts; their existence never indicates successful lock.
    ``record_path`` is an intended location and can be absent on storage failure.
    """
    review_id = uuid4()
    folder = workspace.path / ".state" / "reviews" / str(review_id)
    record = folder / "result.json"
    result_written = False
    error_code = "REVIEW_INPUT_INVALID"

    def finish(status, *, jobs=(), error=None):
        nonlocal result_written, error_code
        error_code = "REVIEW_STORAGE_ERROR"
        result = ProjectReviewResult(
            status=status,
            review_id=review_id,
            scope_locked=status == "locked",
            jobs=jobs,
            record_path=str(record),
            error_code=error,
        )
        _write(record, result)
        result_written = True
        if _load(record, ProjectReviewResult) != result:
            raise _ReviewStop("REVIEW_RECORD_CHANGED")
        return result

    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise _ReviewStop("REVIEW_PLATFORM_UNAVAILABLE")
        # Detach the immutable input snapshot from caller-owned model references.
        request = parse_project_review_json(request.model_dump_json().encode())
        with _mutex(workspace):
            error_code = "REVIEW_SCOPE_INVALID"
            project = _current(workspace)
            if (
                request.discovery.project_id != project.project_id
                or request.discovery.project_context_digest != digest(project)
            ):
                raise _ReviewStop("REVIEW_SCOPE_CHANGED")
            path = workspace.path / "dimensions.md"
            for suffix in (".proposal.json", ".lock.json"):
                target = path.with_suffix(suffix)
                _safe_file(target, optional=True)
                if target.exists():
                    raise _ReviewStop("REVIEW_EXISTING_ARTIFACT")
            if _read(path) != DRAFT_DIMENSIONS.encode():
                raise _ReviewStop("REVIEW_EXISTING_ARTIFACT")
            readers = project.readers or request.readers
            if not readers or (project.readers and request.readers and request.readers != readers):
                raise _ReviewStop("REVIEW_READERS_INVALID")
            error_code = "REVIEW_STORAGE_ERROR"
            _mkdir(folder.parent)
            folder.mkdir()
            _safe_directory(folder)
            for name, value in (
                ("request.json", request),
                ("project.json", project),
                ("discovery.json", request.discovery),
            ):
                _write(folder / name, value)

            audited_decisions = {}
            derived_records = {}

            def verify_records():
                for name, model, expected in (
                    ("request.json", ProjectReviewRequest, request),
                    ("project.json", ProjectContext, project),
                    ("discovery.json", DiscoveryProposal, request.discovery),
                ):
                    if _load(folder / name, model) != expected:
                        raise _ReviewStop("REVIEW_RECORD_CHANGED")
                for stage, (question, decision) in audited_decisions.items():
                    if (
                        _load(folder / f"{stage}-question.json", ReviewQuestion) != question
                        or _load(folder / f"{stage}-decision.json", _Decision) != decision
                    ):
                        raise _ReviewStop("REVIEW_RECORD_CHANGED")
                for checked, (model, expected) in derived_records.items():
                    if _load(checked, model) != expected:
                        raise _ReviewStop("REVIEW_RECORD_CHANGED")

            baseline_hash = _sha(_read(path))
            profile_hash = _sha(_read(workspace.path / "profile.md"))
            question = _question(
                review_id, "competitors", project, request, _candidate_markdown(request), {}
            )
            error_code = "REVIEW_UI_ERROR"
            decision = _ask(folder, question, confirmer)
            audited_decisions[question.stage] = (question, decision)
            if not decision.accepted:
                return finish("rejected")
            error_code = "REVIEW_SCOPE_CHANGED"
            _unchanged(workspace, project, baseline_hash, profile_hash)
            verify_records()
            approval = confirm_competitors(
                request.discovery,
                request.selected_ids,
                expected_digest=digest(request.discovery),
                approved_at=datetime.now(timezone.utc),
            )
            error_code = "REVIEW_STORAGE_ERROR"
            _write(folder / "competitor-approval.json", approval)
            derived_records[folder / "competitor-approval.json"] = (CompetitorApproval, approval)
            if _load(folder / "competitor-approval.json", CompetitorApproval) != approval:
                raise _ReviewStop("REVIEW_RECORD_CHANGED")
            error_code = "REVIEW_DIMENSIONS_INVALID"
            dimensions = design_dimensions(
                project,
                request.discovery,
                approval,
                request.library,
                extension_ids=request.extension_ids,
                selection_rationale=request.selection_rationale,
                readers=readers,
                core_field_ids=request.core_field_ids,
            )
            markdown = render_dimensions(project, request.discovery, approval, dimensions)
            error_code = "REVIEW_STORAGE_ERROR"
            for suffix in (".proposal.json", ".lock.json"):
                _safe_file(path.with_suffix(suffix), optional=True)
            write_dimensions(path, project, request.discovery, approval, dimensions)
            _write(folder / "dimension-proposal.json", dimensions)
            for checked in (folder / "dimension-proposal.json", path.with_suffix(".proposal.json")):
                derived_records[checked] = (DimensionProposal, dimensions)
            markdown_hash = _sha(markdown.encode())
            question = _question(
                review_id,
                "dimensions",
                project,
                request,
                markdown,
                {"dimensions": digest(dimensions), "markdown": markdown_hash},
            )
            error_code = "REVIEW_UI_ERROR"
            decision = _ask(folder, question, confirmer)
            audited_decisions[question.stage] = (question, decision)
            if not decision.accepted:
                return finish("rejected")
            error_code = "REVIEW_ARTIFACT_CHANGED"
            current = _unchanged(workspace, project, markdown_hash, profile_hash)
            verify_records()
            for checked, model, expected in (
                (folder / "competitor-approval.json", CompetitorApproval, approval),
                (folder / "dimension-proposal.json", DimensionProposal, dimensions),
                (path.with_suffix(".proposal.json"), DimensionProposal, dimensions),
            ):
                if _load(checked, model) != expected:
                    raise _ReviewStop("REVIEW_RECORD_CHANGED")
            _safe_file(path.with_suffix(".lock.json"), optional=True)
            error_code = "REVIEW_LOCK_WRITE_FAILED"
            lock = confirm_dimensions(
                path,
                current,
                request.discovery,
                approval,
                dimensions,
                expected_digest=digest(dimensions),
                approved_at=datetime.now(timezone.utc),
            )
            error_code = "REVIEW_LOCK_READBACK_FAILED"
            current = _unchanged(workspace, project, markdown_hash, profile_hash)
            if _load(path.with_suffix(".lock.json"), DimensionLock) != lock:
                raise _ReviewStop("REVIEW_RECORD_CHANGED")
            jobs = prepare_collection_jobs(
                path, current, request.discovery, approval, dimensions, lock
            )
            error_code = "REVIEW_STORAGE_ERROR"
            _write(folder / "dimension-lock.json", lock)
            derived_records[folder / "dimension-lock.json"] = (DimensionLock, lock)
            derived_records[path.with_suffix(".lock.json")] = (DimensionLock, lock)
            if _load(folder / "dimension-lock.json", DimensionLock) != lock:
                raise _ReviewStop("REVIEW_RECORD_CHANGED")
            verify_records()
            return finish("locked", jobs=jobs)
    except (_ReviewStop, ValueError, OSError, RuntimeError, ImportError) as error:
        code = str(error) if isinstance(error, _ReviewStop) else error_code
        # A failed final audit read cannot leave our own success receipt in place.
        if result_written:
            try:
                _safe_file(record)
                record.unlink()
            except (OSError, _ReviewStop):
                pass
        result = ProjectReviewResult(
            status="failed",
            review_id=review_id,
            scope_locked=False,
            record_path=str(record),
            error_code=code,
        )
        try:
            _write(record, result)
        except (OSError, ValueError, _ReviewStop):
            pass
        return result
