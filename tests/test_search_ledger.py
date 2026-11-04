import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from steal_her_heart.memory import MemoryStore
from steal_her_heart.search_ledger import SearchLedger, SearchLedgerError, SearchReservation

FINGERPRINT = "a" * 64


def reserve(ledger, task_id, request_id=None, *, max_calls=30, fingerprint=FINGERPRINT):
    return ledger.reserve(
        task_id=task_id,
        request_id=request_id or uuid4(),
        fingerprint=fingerprint,
        max_calls=max_calls,
    )


def assert_error(code, function, *args, **kwargs):
    with pytest.raises(SearchLedgerError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert caught.value.message == str(caught.value)
    return caught.value


def test_missing_ledger_construction_and_usage_do_not_create_files(tmp_path):
    path = tmp_path / "absent" / "search.sqlite3"
    ledger = SearchLedger(path)
    assert ledger.usage(uuid4()) == (0, 0)
    assert not path.parent.exists()


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, "30", 2**63])
def test_invalid_limit_is_rejected_before_creating_state(tmp_path, limit):
    path = tmp_path / "absent" / "search.sqlite3"
    assert_error("SEARCH_LEDGER_INVALID", reserve, SearchLedger(path), uuid4(), max_calls=limit)
    assert not path.parent.exists()


@pytest.mark.parametrize("value", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 64])
def test_fingerprint_must_be_an_exact_sha256_digest(tmp_path, value):
    path = tmp_path / "absent" / "search.sqlite3"
    assert_error("SEARCH_LEDGER_INVALID", reserve, SearchLedger(path), uuid4(), fingerprint=value)
    assert not path.parent.exists()


def test_reservation_is_durable_and_default_budget_is_thirty(tmp_path):
    path = tmp_path / "state/search.sqlite3"
    task_id = uuid4()
    result = reserve(SearchLedger(path), task_id)
    assert result.status == "reserved" and result.used_calls == 1 and result.max_calls == 30
    assert result.response_json is result.error_code is result.error_message is None
    assert SearchLedger(path).usage(task_id) == (1, 30)
    assert SearchLedger(path).usage(uuid4()) == (0, 0)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM search_tasks").fetchone()[0] == 1
        assert db.execute("SELECT created_at, finished_at FROM search_calls").fetchone()[1] is None


def test_success_replays_after_restart_even_when_budget_is_exhausted(tmp_path):
    path = tmp_path / "search.sqlite3"
    ledger = SearchLedger(path)
    task_id, request_id = uuid4(), uuid4()
    first = reserve(ledger, task_id, request_id, max_calls=1)
    payload = json.dumps({"results": [{"title": "Fixture result", "url": "https://example.com"}]})
    completed = ledger.finish_success(request_id, payload)
    assert completed.status == "succeeded" and completed.response_json == payload
    restored = reserve(SearchLedger(path), task_id, request_id, max_calls=1)
    assert restored == completed
    assert first.request_id == restored.request_id
    assert_error("SEARCH_BUDGET_EXCEEDED", reserve, ledger, task_id, max_calls=1)
    assert ledger.usage(task_id) == (1, 1)


def test_failure_is_saved_replayed_and_still_consumes_budget(tmp_path):
    path = tmp_path / "search.sqlite3"
    task_id, request_id = uuid4(), uuid4()
    ledger = SearchLedger(path)
    reserve(ledger, task_id, request_id, max_calls=1)
    final = ledger.finish_error(
        request_id, "SEARCH_BLOCKED", "Provider requires human verification"
    )
    assert final.status == "failed" and final.response_json is None
    assert final.error_code == "SEARCH_BLOCKED"
    assert final.error_message == "Provider requires human verification"
    assert reserve(SearchLedger(path), task_id, request_id, max_calls=1) == final
    assert_error("SEARCH_BUDGET_EXCEEDED", reserve, ledger, task_id, max_calls=1)
    assert ledger.usage(task_id) == (1, 1)


def test_unknown_in_flight_call_never_authorizes_a_retry_after_restart(tmp_path):
    path = tmp_path / "search.sqlite3"
    task_id, request_id = uuid4(), uuid4()
    reserve(SearchLedger(path), task_id, request_id)
    assert_error("SEARCH_IN_FLIGHT", reserve, SearchLedger(path), task_id, request_id)
    assert SearchLedger(path).usage(task_id) == (1, 30)


@pytest.mark.parametrize("change", ["task", "fingerprint"])
def test_request_id_cannot_be_rebound_or_leak_cached_result(tmp_path, change):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id, request_id = uuid4(), uuid4()
    reserve(ledger, task_id, request_id)
    ledger.finish_success(request_id, '{"query":"fixture"}')
    other_task = uuid4() if change == "task" else task_id
    fingerprint = "b" * 64 if change == "fingerprint" else FINGERPRINT
    assert_error(
        "SEARCH_REQUEST_CONFLICT",
        reserve,
        ledger,
        other_task,
        request_id,
        fingerprint=fingerprint,
    )
    assert ledger.usage(task_id) == (1, 30)
    if change == "task":
        assert ledger.usage(other_task) == (0, 0)


@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("completed_id", [False, True])
def test_saved_budget_cannot_be_changed_by_new_or_replayed_requests(tmp_path, limit, completed_id):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id, request_id = uuid4(), uuid4()
    reserve(ledger, task_id, request_id, max_calls=2)
    ledger.finish_success(request_id, "{}")
    assert_error(
        "SEARCH_BUDGET_CONFIG",
        reserve,
        ledger,
        task_id,
        request_id if completed_id else None,
        max_calls=limit,
    )
    assert ledger.usage(task_id) == (1, 2)


def test_distinct_tasks_have_independent_limits(tmp_path):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    first, second = uuid4(), uuid4()
    reserve(ledger, first, max_calls=1)
    reserve(ledger, second, max_calls=2)
    reserve(ledger, second, max_calls=2)
    assert ledger.usage(first) == (1, 1)
    assert ledger.usage(second) == (2, 2)


def test_concurrent_new_calls_and_first_schema_creation_never_exceed_budget(tmp_path):
    path = tmp_path / "newdir/search.sqlite3"
    task_id = uuid4()

    def attempt(_):
        try:
            return reserve(SearchLedger(path), task_id, max_calls=3).status
        except SearchLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(24)))
    assert results.count("reserved") == 3
    assert results.count("SEARCH_BUDGET_EXCEEDED") == 21
    assert SearchLedger(path).usage(task_id) == (3, 3)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM search_calls").fetchone()[0] == 3


def test_concurrent_identical_request_only_has_one_network_authorization(tmp_path):
    path = tmp_path / "search.sqlite3"
    task_id, request_id = uuid4(), uuid4()

    def attempt(_):
        try:
            return reserve(SearchLedger(path), task_id, request_id).status
        except SearchLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(16)))
    assert results.count("reserved") == 1
    assert results.count("SEARCH_IN_FLIGHT") == 15
    assert SearchLedger(path).usage(task_id) == (1, 30)


@pytest.mark.parametrize("later", ["success", "error"])
def test_final_result_cannot_be_overwritten(tmp_path, later):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id, request_id = uuid4(), uuid4()
    reserve(ledger, task_id, request_id)
    saved = ledger.finish_success(request_id, '{"value":"first"}')
    if later == "success":
        assert_error("SEARCH_ALREADY_FINALIZED", ledger.finish_success, request_id, "{}")
    else:
        assert_error("SEARCH_ALREADY_FINALIZED", ledger.finish_error, request_id, "FAILED", "safe")
    assert reserve(ledger, task_id, request_id) == saved


def test_concurrent_finalizers_cannot_replace_each_other(tmp_path):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id, request_id = uuid4(), uuid4()
    reserve(ledger, task_id, request_id)

    def finish(index):
        try:
            return ledger.finish_success(request_id, json.dumps({"index": index})).status
        except SearchLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(finish, range(12)))
    assert results.count("succeeded") == 1
    assert results.count("SEARCH_ALREADY_FINALIZED") == 11


@pytest.mark.parametrize("method", ["success", "error"])
def test_finishing_unknown_request_does_not_create_a_database(tmp_path, method):
    path = tmp_path / "absent/search.sqlite3"
    ledger = SearchLedger(path)
    if method == "success":
        assert_error("SEARCH_REQUEST_UNKNOWN", ledger.finish_success, uuid4(), "{}")
    else:
        assert_error("SEARCH_REQUEST_UNKNOWN", ledger.finish_error, uuid4(), "FAILED", "Safe")
    assert not path.parent.exists()


@pytest.mark.parametrize("payload", ["", "malformed-secret", "[]", "null", '{"x":NaN}', 5])
def test_invalid_cached_payload_is_rejected_without_touching_state(tmp_path, payload):
    path = tmp_path / "absent/search.sqlite3"
    error = assert_error(
        "SEARCH_LEDGER_INVALID", SearchLedger(path).finish_success, uuid4(), payload
    )
    assert "malformed-secret" not in str(error)
    assert not path.parent.exists()


def test_database_failure_rolls_back_both_budget_and_call_without_exposing_raw_error(tmp_path):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id = uuid4()
    reserve(ledger, task_id)
    with sqlite3.connect(ledger.path) as db:
        db.execute("""CREATE TRIGGER fail_call BEFORE INSERT ON search_calls
                      BEGIN SELECT RAISE(ABORT, 'test-sensitive-database-message'); END""")
    error = assert_error("SEARCH_LEDGER_UNAVAILABLE", reserve, ledger, task_id)
    assert "test-sensitive" not in str(error)
    assert ledger.usage(task_id) == (1, 30)
    new_task = uuid4()
    assert_error("SEARCH_LEDGER_UNAVAILABLE", reserve, ledger, new_task)
    assert ledger.usage(new_task) == (0, 0)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT COUNT(*) FROM search_calls").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["unknown_version", "unrelated_empty_version", "memory_store"])
def test_unknown_or_unrelated_database_is_rejected_without_modifying_it(tmp_path, kind):
    path = tmp_path / "state.sqlite3"
    if kind == "memory_store":
        MemoryStore(path)
    else:
        with sqlite3.connect(path) as db:
            if kind == "unknown_version":
                db.execute("PRAGMA user_version=99")
            else:
                db.execute("CREATE TABLE unrelated (value TEXT)")
    before = path.read_bytes()
    ledger = SearchLedger(path)
    assert_error("SEARCH_LEDGER_SCHEMA", reserve, ledger, uuid4())
    assert_error("SEARCH_LEDGER_SCHEMA", ledger.usage, uuid4())
    assert path.read_bytes() == before


def test_invalid_database_data_is_not_replayed_as_a_success(tmp_path):
    ledger = SearchLedger(tmp_path / "search.sqlite3")
    task_id, request_id = uuid4(), uuid4()
    reserve(ledger, task_id, request_id)
    ledger.finish_success(request_id, "{}")
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE search_calls SET response_json='corrupt-secret' WHERE request_id=?",
            (str(request_id),),
        )
    error = assert_error("SEARCH_LEDGER_UNAVAILABLE", reserve, ledger, task_id, request_id)
    assert "corrupt-secret" not in str(error)
    assert ledger.usage(task_id) == (1, 30)


def test_reservation_contract_is_frozen_and_rejects_inconsistent_states():
    fields = {"task_id": uuid4(), "request_id": uuid4(), "used_calls": 1, "max_calls": 2}
    reservation = SearchReservation(status="reserved", **fields)
    with pytest.raises(ValidationError):
        reservation.used_calls = 2
    for update in (
        {"status": "succeeded"},
        {"status": "failed", "error_code": "FAILED"},
        {"status": "reserved", "response_json": "{}"},
        {"status": "reserved", "used_calls": 3},
    ):
        with pytest.raises(ValidationError):
            SearchReservation.model_validate({**fields, **update})
