"""Durable search intent and per-task call budgets; never retry unknown in-flight calls."""

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import Field, ValidationError, field_validator, model_validator

from .schemas import Contract, Text, utc_now

_APPLICATION_ID = 0x53484853
_SCHEMA_VERSION = 1
_LIMIT = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
_FINGERPRINT = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
_RESPONSE_JSON = Annotated[str, Field(strict=True, min_length=2, max_length=1048576)]
_ERROR_CODE = Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]


def _json_object(value: str) -> str:
    def invalid_constant(value: str):
        raise ValueError("Non-finite numbers are not valid JSON")

    if not isinstance(json.loads(value, parse_constant=invalid_constant), dict):
        raise ValueError("A search response must be a JSON object")
    return value


class SearchLedgerError(RuntimeError):
    """Only fixed ledger messages, never the raw database exception or payload."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class SearchReservation(Contract):
    task_id: UUID
    request_id: UUID
    status: Literal["reserved", "succeeded", "failed"]
    response_json: Optional[_RESPONSE_JSON] = None
    error_code: Optional[_ERROR_CODE] = None
    error_message: Optional[Text] = None
    used_calls: _LIMIT
    max_calls: _LIMIT

    @field_validator("response_json")
    @classmethod
    def check_response(cls, value: Optional[str]) -> Optional[str]:
        return _json_object(value) if value is not None else None

    @model_validator(mode="after")
    def check_state(self) -> "SearchReservation":
        if self.used_calls > self.max_calls:
            raise ValueError("Used search calls exceed the task budget")
        if self.status == "reserved":
            if any(
                value is not None
                for value in (
                    self.response_json,
                    self.error_code,
                    self.error_message,
                )
            ):
                raise ValueError("Reserved calls cannot carry a final result")
        elif self.status == "succeeded":
            if self.response_json is None or self.error_code or self.error_message:
                raise ValueError("Succeeded calls require only a response")
        elif self.response_json is not None or not self.error_code or not self.error_message:
            raise ValueError("Failed calls require only a classified safe error")
        return self


class _ReserveInput(Contract):
    task_id: UUID
    request_id: UUID
    fingerprint: _FINGERPRINT
    max_calls: _LIMIT = 30

    @field_validator("fingerprint", mode="before")
    @classmethod
    def check_exact_digest(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Fingerprint must be an exact SHA-256 digest")
        return value


class _SuccessInput(Contract):
    request_id: UUID
    response_json: _RESPONSE_JSON

    @field_validator("response_json")
    @classmethod
    def check_json_object(cls, value: str) -> str:
        return _json_object(value)


class _FailureInput(Contract):
    request_id: UUID
    code: _ERROR_CODE
    message: Text


_COLUMNS = {
    "search_tasks": ("task_id", "max_calls", "used_calls"),
    "search_calls": (
        "request_id",
        "task_id",
        "fingerprint",
        "status",
        "response_json",
        "error_code",
        "error_message",
        "created_at",
        "finished_at",
    ),
}


class SearchLedger:
    """Each request ID is single-use and permanently consumes its reserved call.

    A repeated completed request replays its saved response/error. A repeated reserved
    request is uncertain and must not authorize another network request. A task's limit
    is fixed at first reservation; this interface cannot silently raise that limit.
    Constructing or reading a missing ledger does not create files or directories.
    """

    _application_id = _APPLICATION_ID

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()

    @staticmethod
    def _validated(model: type[Contract], values: dict) -> Contract:
        try:
            return model.model_validate(values)
        except (ValidationError, ValueError, TypeError):
            raise SearchLedgerError(
                "SEARCH_LEDGER_INVALID", "Search ledger arguments are invalid"
            ) from None

    @classmethod
    def _check_schema(cls, db: sqlite3.Connection, *, initialize: bool) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        application = db.execute("PRAGMA application_id").fetchone()[0]
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if initialize and version == application == 0 and not tables:
            db.execute("""CREATE TABLE search_tasks (
                task_id TEXT PRIMARY KEY,
                max_calls INTEGER NOT NULL CHECK(max_calls > 0),
                used_calls INTEGER NOT NULL CHECK(used_calls >= 0 AND used_calls <= max_calls)
            )""")
            db.execute("""CREATE TABLE search_calls (
                request_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES search_tasks(task_id),
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved', 'succeeded', 'failed')),
                response_json TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                CHECK(
                    (status='reserved' AND response_json IS NULL AND error_code IS NULL
                        AND error_message IS NULL AND finished_at IS NULL)
                    OR (status='succeeded' AND response_json IS NOT NULL AND error_code IS NULL
                        AND error_message IS NULL AND finished_at IS NOT NULL)
                    OR (status='failed' AND response_json IS NULL AND error_code IS NOT NULL
                        AND error_message IS NOT NULL AND finished_at IS NOT NULL)
                )
            )""")
            db.execute(f"PRAGMA application_id = {cls._application_id}")
            db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return
        if (
            version != _SCHEMA_VERSION
            or application != cls._application_id
            or tables != set(_COLUMNS)
        ):
            raise SearchLedgerError("SEARCH_LEDGER_SCHEMA", "Unsupported search ledger schema")
        for table, columns in _COLUMNS.items():
            actual = tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
            if actual != columns:
                raise SearchLedgerError("SEARCH_LEDGER_SCHEMA", "Unsupported search ledger schema")

    @contextmanager
    def _write(self, *, initialize: bool = False) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            if initialize:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(str(self.path), timeout=1)
            else:
                if not self.path.exists():
                    raise SearchLedgerError(
                        "SEARCH_REQUEST_UNKNOWN", "Search request is not present in the ledger"
                    )
                db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=1)
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            self._check_schema(db, initialize=initialize)
            yield db
            db.commit()
        except SearchLedgerError:
            if db is not None:
                db.rollback()
            raise
        except (OSError, sqlite3.Error, ValidationError):
            if db is not None:
                db.rollback()
            raise SearchLedgerError(
                "SEARCH_LEDGER_UNAVAILABLE", "Search call ledger could not be accessed safely"
            ) from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _find(db: sqlite3.Connection, request_id: UUID) -> Optional[tuple]:
        return db.execute(
            """SELECT c.task_id, c.request_id, c.fingerprint, c.status, c.response_json,
                      c.error_code, c.error_message, t.used_calls, t.max_calls
               FROM search_calls c JOIN search_tasks t ON c.task_id=t.task_id
               WHERE c.request_id=?""",
            (str(request_id),),
        ).fetchone()

    @staticmethod
    def _reservation(row: tuple) -> SearchReservation:
        return SearchReservation(
            task_id=row[0],
            request_id=row[1],
            status=row[3],
            response_json=row[4],
            error_code=row[5],
            error_message=row[6],
            used_calls=row[7],
            max_calls=row[8],
        )

    def reserve(
        self, *, task_id: UUID, request_id: UUID, fingerprint: str, max_calls: int = 30
    ) -> SearchReservation:
        arguments = self._validated(
            _ReserveInput,
            {
                "task_id": task_id,
                "request_id": request_id,
                "fingerprint": fingerprint,
                "max_calls": max_calls,
            },
        )
        with self._write(initialize=True) as db:
            existing = self._find(db, arguments.request_id)
            if existing is not None:
                if existing[0] != str(arguments.task_id) or existing[2] != arguments.fingerprint:
                    raise SearchLedgerError(
                        "SEARCH_REQUEST_CONFLICT", "Search request ID belongs to another request"
                    )
                if existing[8] != arguments.max_calls:
                    raise SearchLedgerError(
                        "SEARCH_BUDGET_CONFIG", "Search task budget differs from its saved limit"
                    )
                if existing[3] == "reserved":
                    raise SearchLedgerError(
                        "SEARCH_IN_FLIGHT", "Search request is unresolved; it will not be resent"
                    )
                return self._reservation(existing)
            budget = db.execute(
                "SELECT used_calls, max_calls FROM search_tasks WHERE task_id=?",
                (str(arguments.task_id),),
            ).fetchone()
            if budget is None:
                db.execute(
                    "INSERT INTO search_tasks VALUES (?, ?, 0)",
                    (str(arguments.task_id), arguments.max_calls),
                )
                budget = (0, arguments.max_calls)
            if budget[1] != arguments.max_calls:
                raise SearchLedgerError(
                    "SEARCH_BUDGET_CONFIG", "Search task budget differs from its saved limit"
                )
            if budget[0] >= budget[1]:
                raise SearchLedgerError(
                    "SEARCH_BUDGET_EXCEEDED", "Search task call budget is exhausted"
                )
            cursor = db.execute(
                "UPDATE search_tasks SET used_calls=used_calls+1 WHERE task_id=?",
                (str(arguments.task_id),),
            )
            if cursor.rowcount != 1:
                raise SearchLedgerError(
                    "SEARCH_LEDGER_UNAVAILABLE", "Search budget could not be reserved safely"
                )
            db.execute(
                """INSERT INTO search_calls
                   (request_id, task_id, fingerprint, status, created_at)
                   VALUES (?, ?, ?, 'reserved', ?)""",
                (
                    str(arguments.request_id),
                    str(arguments.task_id),
                    arguments.fingerprint,
                    utc_now().isoformat(),
                ),
            )
            return self._reservation(self._find(db, arguments.request_id))

    def _finish(
        self,
        request_id: UUID,
        *,
        response_json: Optional[str] = None,
        code: Optional[str] = None,
        message: Optional[str] = None,
    ) -> SearchReservation:
        with self._write() as db:
            existing = self._find(db, request_id)
            if existing is None:
                raise SearchLedgerError(
                    "SEARCH_REQUEST_UNKNOWN", "Search request is not present in the ledger"
                )
            if existing[3] != "reserved":
                raise SearchLedgerError(
                    "SEARCH_ALREADY_FINALIZED", "Search request already has a final result"
                )
            status = "succeeded" if response_json is not None else "failed"
            cursor = db.execute(
                """UPDATE search_calls SET status=?, response_json=?, error_code=?,
                   error_message=?, finished_at=? WHERE request_id=? AND status='reserved'""",
                (status, response_json, code, message, utc_now().isoformat(), str(request_id)),
            )
            if cursor.rowcount != 1:
                raise SearchLedgerError(
                    "SEARCH_LEDGER_UNAVAILABLE", "Search result could not be saved safely"
                )
            return self._reservation(self._find(db, request_id))

    def finish_success(self, request_id: UUID, response_json: str) -> SearchReservation:
        arguments = self._validated(
            _SuccessInput,
            {
                "request_id": request_id,
                "response_json": response_json,
            },
        )
        return self._finish(arguments.request_id, response_json=arguments.response_json)

    def finish_error(self, request_id: UUID, code: str, message: str) -> SearchReservation:
        """Persist a classified code and safe message supplied by the trusted tool."""
        arguments = self._validated(
            _FailureInput,
            {
                "request_id": request_id,
                "code": code,
                "message": message,
            },
        )
        return self._finish(arguments.request_id, code=arguments.code, message=arguments.message)

    def usage(self, task_id: UUID) -> tuple[int, int]:
        arguments = self._validated(
            _ReserveInput,
            {
                "task_id": task_id,
                "request_id": task_id,
                "fingerprint": "0" * 64,
            },
        )
        if not self.path.exists():
            return (0, 0)
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
            self._check_schema(db, initialize=False)
            row = db.execute(
                "SELECT used_calls, max_calls FROM search_tasks WHERE task_id=?",
                (str(arguments.task_id),),
            ).fetchone()
            if row is None:
                return (0, 0)
            if type(row[0]) is not int or type(row[1]) is not int or not 0 <= row[0] <= row[1]:
                raise SearchLedgerError("SEARCH_LEDGER_SCHEMA", "Unsupported search ledger schema")
            return row
        except SearchLedgerError:
            raise
        except (OSError, sqlite3.Error):
            raise SearchLedgerError(
                "SEARCH_LEDGER_UNAVAILABLE", "Search call ledger could not be accessed safely"
            ) from None
        finally:
            if db is not None:
                db.close()
