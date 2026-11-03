"""Atomic local snapshots and state events; semantic memory reuse is phase 6."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from .schemas import ResearchState, StateEvent, TaskSnapshot


class ConcurrentUpdate(RuntimeError):
    pass


class UnsupportedSchema(ValueError):
    pass


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise UnsupportedSchema(f"Unsupported memory schema version: {version}")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                task_id TEXT NOT NULL REFERENCES tasks(task_id), revision INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (task_id, revision))""")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=5)
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, snapshot: TaskSnapshot) -> None:
        snapshot = TaskSnapshot.model_validate_json(snapshot.model_dump_json())
        context = snapshot.context
        if context.revision != 0 or context.current_state != ResearchState.CREATED:
            raise ValueError("New tasks must start in CREATED at revision 0")
        with self._db() as db:
            db.execute(
                "INSERT INTO tasks VALUES (?, ?, ?)",
                (str(context.task_id), 0, snapshot.model_dump_json()),
            )
            self._event(db, snapshot)

    def load(self, task_id: UUID) -> TaskSnapshot:
        with self._db() as db:
            row = db.execute(
                "SELECT snapshot FROM tasks WHERE task_id = ?", (str(task_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return TaskSnapshot.model_validate_json(row[0])

    def commit(self, snapshot: TaskSnapshot, *, expected_revision: int) -> None:
        snapshot = TaskSnapshot.model_validate_json(snapshot.model_dump_json())
        context = snapshot.context
        if context.revision != expected_revision + 1:
            raise ValueError("A commit must advance exactly one revision")
        with self._db() as db:
            cursor = db.execute(
                """UPDATE tasks SET revision = ?, snapshot = ?
                WHERE task_id = ? AND revision = ?""",
                (
                    context.revision,
                    snapshot.model_dump_json(),
                    str(context.task_id),
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrentUpdate("Snapshot has changed; reload before continuing")
            self._event(db, snapshot)

    @staticmethod
    def _event(db: sqlite3.Connection, snapshot: TaskSnapshot) -> None:
        event = StateEvent(
            task_id=snapshot.context.task_id,
            revision=snapshot.context.revision,
            state=snapshot.context.current_state,
        )
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?)",
            (
                str(event.task_id),
                event.revision,
                event.model_dump_json(),
            ),
        )

    def events(self, task_id: UUID) -> tuple[StateEvent, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT payload FROM events WHERE task_id = ? ORDER BY revision", (str(task_id),)
            ).fetchall()
        return tuple(StateEvent.model_validate_json(row[0]) for row in rows)
