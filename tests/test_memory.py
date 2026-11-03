import sqlite3

import pytest

from steal_her_heart.memory import ConcurrentUpdate, MemoryStore, UnsupportedSchema
from steal_her_heart.schemas import ResearchMemory, ResearchState, StateContext, TaskSnapshot
from steal_her_heart.state_machine import transition


def new_snapshot():
    context = StateContext(user_input="分析 Cursor")
    return TaskSnapshot(context=context, memory=ResearchMemory(task_id=context.task_id))


def test_restart_keeps_state_memory_and_events(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first = MemoryStore(path)
    snapshot = new_snapshot()
    first.create(snapshot)
    updated = TaskSnapshot(
        context=transition(snapshot.context, ResearchState.UNDERSTANDING), memory=snapshot.memory
    )
    first.commit(updated, expected_revision=0)
    restarted = MemoryStore(path)
    assert restarted.load(snapshot.context.task_id) == updated
    assert [e.revision for e in restarted.events(snapshot.context.task_id)] == [0, 1]


def test_stale_writer_cannot_replace_new_snapshot_or_add_event(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first, second = MemoryStore(path), MemoryStore(path)
    snapshot = new_snapshot()
    first.create(snapshot)
    updated = TaskSnapshot(
        context=transition(snapshot.context, ResearchState.UNDERSTANDING), memory=snapshot.memory
    )
    first.commit(updated, expected_revision=0)
    with pytest.raises(ConcurrentUpdate):
        second.commit(updated, expected_revision=0)
    assert len(first.events(snapshot.context.task_id)) == 2
    assert first.load(snapshot.context.task_id) == updated


def test_event_failure_rolls_back_snapshot(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    snapshot = new_snapshot()
    store.create(snapshot)
    with sqlite3.connect(str(store.path)) as db:
        db.execute("""CREATE TRIGGER fail_event BEFORE INSERT ON events
                      BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    updated = TaskSnapshot(
        context=transition(snapshot.context, ResearchState.UNDERSTANDING), memory=snapshot.memory
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.commit(updated, expected_revision=0)
    assert store.load(snapshot.context.task_id) == snapshot


def test_unknown_storage_version_rejected(tmp_path):
    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(str(path)) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(UnsupportedSchema):
        MemoryStore(path)
