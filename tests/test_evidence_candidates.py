"""Explicit local selections become exact unvalidated candidates, never facts."""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from steal_her_heart.evidence_candidates import (
    EvidenceCandidate,
    ExtractionError,
    QuoteLocator,
    TextSpan,
    extract_candidates,
    verify_candidate,
)
from steal_her_heart.schemas import Evidence
from steal_her_heart.source_snapshots import SnapshotStore

OLD = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
TEXT = "第一行 🌏\n重复文字，重复文字。\n按次计费，条件未核验。\n"


def stored(tmp_path, text=TEXT, *, url="https://example.com/product", fetched_at=OLD):
    store = SnapshotStore(tmp_path / "sources")
    document = store.save(
        url=url,
        body=text.encode(),
        media_type="text/plain",
        charset="utf-8",
        fetched_at=fetched_at,
    )
    return store, store.load(document.source.snapshot_ref)


def extract(snapshot, *, competitor="任意产品", dimension="商业模式", spans=None):
    return extract_candidates(
        snapshot,
        competitor=competitor,
        dimension=dimension,
        spans=spans if spans is not None else (TextSpan(start=0, end=5),),
    )


@pytest.mark.parametrize(
    "target,dimension",
    [
        ("工业检测设备", "维护模式"),
        ("餐饮品牌", "服务方式"),
        ("开源软件", "授权方式"),
        ("从未预设的行业", "自定义能力维度"),
    ],
)
def test_open_target_labels_do_not_claim_entity_or_dimension_support(tmp_path, target, dimension):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot, competitor=target, dimension=dimension)
    assert candidate.competitor == target and candidate.dimension == dimension
    assert candidate.fact == candidate.quote == "第一行 🌏"
    assert candidate.source == snapshot.document.source
    assert candidate.source.source_name == "example.com"
    assert candidate.source.fetched_at == OLD and candidate.source.published_at is None
    assert candidate.support_status == "unvalidated" and candidate.method == "verbatim_span"
    assert not isinstance(candidate, Evidence)
    assert "confidence" not in candidate.model_dump() and "validation" not in candidate.model_dump()
    assert verify_candidate(candidate, snapshot) is None


def test_offsets_are_unicode_code_points_and_preserve_newline_at_end(tmp_path):
    _, snapshot = stored(tmp_path)
    start, end = snapshot.text.index("🌏"), snapshot.text.index("重复文字")
    (candidate,) = extract(snapshot, spans=(TextSpan(start=start, end=end),))
    assert candidate.quote == "🌏\n" and candidate.fact == candidate.quote
    assert candidate.locator.start == 4 and candidate.locator.end == 6
    assert candidate.locator.start_line == candidate.locator.end_line == 1
    assert candidate.locator.snapshot_ref == snapshot.document.source.snapshot_ref
    assert candidate.locator.text_sha256 == hashlib.sha256(snapshot.text.encode()).hexdigest()
    assert candidate.locator.parser_version == "1"
    roundtrip = EvidenceCandidate.model_validate_json(candidate.model_dump_json())
    assert roundtrip.quote == "🌏\n" and roundtrip == candidate


def test_multiline_selection_has_unambiguous_inclusive_line_numbers(tmp_path):
    _, snapshot = stored(tmp_path)
    start = snapshot.text.index("🌏")
    end = snapshot.text.index("按次") + 2
    (candidate,) = extract(snapshot, spans=(TextSpan(start=start, end=end),))
    assert candidate.quote == snapshot.text[start:end]
    assert candidate.locator.start_line == 1 and candidate.locator.end_line == 3
    assert verify_candidate(candidate, snapshot) is None


def test_repeated_text_remains_distinguishable_by_span_and_order(tmp_path):
    _, snapshot = stored(tmp_path)
    a = snapshot.text.index("重复文字")
    b = snapshot.text.index("重复文字", a + 1)
    spans = (TextSpan(start=b, end=b + 4), TextSpan(start=a, end=a + 4))
    second, first = extract(snapshot, spans=spans)
    assert first.quote == second.quote == "重复文字"
    assert first.id != second.id and second.locator.start == b
    assert extract(snapshot, spans=spans) == (second, first)


def test_overlapping_distinct_spans_are_allowed_without_deduplicating_context(tmp_path):
    _, snapshot = stored(tmp_path)
    result = extract(snapshot, spans=(TextSpan(start=0, end=5), TextSpan(start=1, end=5)))
    assert len(result) == 2 and result[0].id != result[1].id


@pytest.mark.parametrize(
    "values",
    [
        {"start": True, "end": 4},
        {"start": 0, "end": False},
        {"start": -1, "end": 4},
        {"start": "0", "end": 4},
        {"start": 0, "end": 1.0},
        {"start": 0, "end": 0},
        {"start": 5, "end": 4},
        {"start": 0, "end": 4001},
        {"start": 0, "end": 2, "extra": True},
    ],
)
def test_span_schema_rejects_non_strict_or_unbounded_selections(values):
    with pytest.raises(ValidationError):
        TextSpan.model_validate(values)


@pytest.mark.parametrize(
    "spans",
    [
        (),
        (TextSpan(start=0, end=1),) * 2,
        (TextSpan(start=0, end=1),) * 21,
        (TextSpan(start=0, end=1), TextSpan(start=100, end=101)),
        (TextSpan(start=5, end=6),),  # Only the first line's newline.
        (TextSpan.model_construct(start=-1, end=3),),
        ({"start": 0, "end": 2, "raw_quote": "ignored"},),
    ],
)
def test_invalid_batch_is_rejected_without_returning_partial_candidates(tmp_path, spans):
    _, snapshot = stored(tmp_path)
    with pytest.raises(ExtractionError) as caught:
        extract(snapshot, spans=spans)
    assert caught.value.code == "EXTRACT_INVALID_SELECTION"


@pytest.mark.parametrize(
    "changes",
    [{"competitor": ""}, {"dimension": " "}, {"competitor": True}, {"dimension": "x" * 4001}],
)
def test_invalid_targets_are_not_silently_filled(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    with pytest.raises(ExtractionError) as caught:
        extract(snapshot, **changes)
    assert caught.value.code == "EXTRACT_INVALID_SELECTION"


def test_non_unicode_target_and_unbounded_iterable_do_not_escape_the_error_boundary(tmp_path):
    _, snapshot = stored(tmp_path)
    with pytest.raises(ExtractionError) as caught:
        extract(snapshot, competitor="\ud800")
    assert caught.value.code == "EXTRACT_INVALID_SELECTION"

    def unbounded():
        while True:
            pytest.fail("Extraction must not consume an unbounded selection iterable")
            yield TextSpan(start=0, end=1)

    with pytest.raises(ExtractionError) as caught:
        extract(snapshot, spans=unbounded())
    assert caught.value.code == "EXTRACT_INVALID_SELECTION"


def test_full_selection_cap_is_enforced_and_long_spans_not_truncated(tmp_path):
    _, snapshot = stored(tmp_path, "x" * 4010)
    result = extract(snapshot, spans=(TextSpan(start=0, end=4000),))
    assert len(result[0].quote) == 4000
    spans = tuple(TextSpan(start=i, end=i + 1) for i in range(20))
    assert len(extract(snapshot, spans=spans)) == 20


def test_identity_binds_source_target_dimension_and_exact_span(tmp_path):
    _, snapshot = stored(tmp_path)
    (original,) = extract(snapshot)
    (target_changed,) = extract(snapshot, competitor="另一对象")
    (dimension_changed,) = extract(snapshot, dimension="另一维度")
    _, newer = stored(tmp_path, fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    (source_changed,) = extract(newer)
    assert len({original.id, target_changed.id, dimension_changed.id, source_changed.id}) == 4
    assert original.id.startswith("candidate-") and len(original.id) == 74


@pytest.mark.parametrize(
    "changes",
    [
        {"raw": b"other raw"},
        {"text": "other text\n"},
        {"raw": "not bytes"},
        {"text": b"not text"},
    ],
)
def test_hand_built_snapshot_values_do_not_bypass_content_hashes(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    with pytest.raises(ExtractionError) as caught:
        extract(replace(snapshot, **changes))
    assert caught.value.code == "EXTRACT_SOURCE_MISMATCH"


@pytest.mark.parametrize(
    "changes",
    [
        {"text_sha256": "0" * 64},
        {"line_count": 99},
        {"title": "Forged title"},
        {"charset": "UTF-8"},
        {"parser_version": "2"},
    ],
)
def test_snapshot_document_mutation_is_not_trusted(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    mutated = replace(snapshot, document=snapshot.document.model_copy(update=changes))
    with pytest.raises(ExtractionError) as caught:
        extract(mutated)
    assert caught.value.code == "EXTRACT_SOURCE_MISMATCH"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "source-" + "0" * 64},
        {"snapshot_ref": "sha256:" + "0" * 64},
        {"url": "https://another.example.org/"},
        {"source_name": "Official verified publisher"},
        {"content_sha256": "0" * 64},
        {"fetched_at": datetime(2026, 11, 3)},
        {"published_at": OLD},
    ],
)
def test_source_metadata_and_canonical_manifest_identity_are_checked(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    source = snapshot.document.source.model_copy(update=changes)
    document = snapshot.document.model_copy(update={"source": source})
    with pytest.raises(ExtractionError) as caught:
        extract(replace(snapshot, document=document))
    assert caught.value.code == "EXTRACT_SOURCE_MISMATCH"


def test_forged_text_and_hash_still_must_be_reconstructed_from_raw(tmp_path):
    _, snapshot = stored(tmp_path)
    text = "Forged interpretation\n"
    document = snapshot.document.model_copy(
        update={"text_sha256": hashlib.sha256(text.encode()).hexdigest(), "line_count": 1}
    )
    with pytest.raises(ExtractionError) as caught:
        extract(replace(snapshot, document=document, text=text))
    assert caught.value.code == "EXTRACT_SOURCE_MISMATCH"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "candidate-" + "0" * 64},
        {"quote": "Different quote"},
        {"quote": "Different quote", "fact": "Different quote"},
        {"fact": "An unsupported paraphrase"},
        {"competitor": "Changed entity"},
        {"dimension": "Changed dimension"},
        {"method": "model"},
        {"support_status": "validated"},
    ],
)
def test_candidate_mutation_fails_mechanical_integrity_check(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot)
    with pytest.raises(ExtractionError) as caught:
        verify_candidate(candidate.model_copy(update=changes), snapshot)
    assert caught.value.code == "EXTRACT_INTEGRITY_ERROR"


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_ref": "sha256:" + "0" * 64},
        {"text_sha256": "0" * 64},
        {"parser_version": "2"},
        {"start": 1},
        {"end": 4},
        {"start_line": 2},
        {"end_line": 2},
    ],
)
def test_locator_tampering_cannot_be_verified(tmp_path, changes):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot)
    locator = candidate.locator.model_copy(update=changes)
    with pytest.raises(ExtractionError) as caught:
        verify_candidate(candidate.model_copy(update={"locator": locator}), snapshot)
    assert caught.value.code == "EXTRACT_INTEGRITY_ERROR"


def test_candidate_from_another_capture_is_not_verified_against_current_snapshot(tmp_path):
    _, previous = stored(tmp_path)
    _, current = stored(tmp_path, fetched_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    (candidate,) = extract(previous)
    with pytest.raises(ExtractionError) as caught:
        verify_candidate(candidate, current)
    assert caught.value.code == "EXTRACT_INTEGRITY_ERROR"


def test_source_field_tampering_on_candidate_is_not_accepted(tmp_path):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot)
    source = candidate.source.model_copy(update={"source_name": "Official"})
    with pytest.raises(ExtractionError) as caught:
        verify_candidate(candidate.model_copy(update={"source": source}), snapshot)
    assert caught.value.code == "EXTRACT_INTEGRITY_ERROR"


def test_error_messages_do_not_echo_selected_source_or_raw_validation_details(tmp_path):
    _, snapshot = stored(tmp_path, "private-source-secret\n")
    bad = replace(snapshot, text="changed-secret\n")
    with pytest.raises(ExtractionError) as caught:
        extract(bad)
    assert "secret" not in str(caught.value)


def test_extraction_and_verification_do_not_write_files_or_refresh_acquisition_time(tmp_path):
    store, snapshot = stored(tmp_path)
    paths = list(store.root.rglob("*"))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths if path.is_file()
    }
    (candidate,) = extract(snapshot)
    verify_candidate(candidate, snapshot)
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths if path.is_file()
    }
    assert candidate.source.fetched_at == OLD
    assert candidate.source.snapshot_ref == snapshot.document.source.snapshot_ref


def test_exact_quote_is_not_stripped_but_blank_quote_cannot_be_a_candidate(tmp_path):
    _, snapshot = stored(tmp_path, "One word\nSecond line")
    (candidate,) = extract(snapshot, spans=(TextSpan(start=3, end=9),))
    assert candidate.quote == " word\n"
    values = candidate.model_dump()
    values["quote"] = values["fact"] = " \n"
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(values)


def test_locator_contract_rejects_unknown_fields_and_non_ordered_lines(tmp_path):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot)
    values = candidate.locator.model_dump()
    with pytest.raises(ValidationError):
        QuoteLocator.model_validate({**values, "end_line": 0})
    with pytest.raises(ValidationError):
        QuoteLocator.model_validate({**values, "start_line": 3})
    with pytest.raises(ValidationError):
        QuoteLocator.model_validate({**values, "url": "https://example.com/"})


@pytest.mark.parametrize("field", ["id", "snapshot_ref", "text_sha256"])
def test_identity_fields_reject_trailing_newline_instead_of_trimming_it(tmp_path, field):
    _, snapshot = stored(tmp_path)
    (candidate,) = extract(snapshot)
    if field == "id":
        values = candidate.model_dump()
        values[field] += "\n"
        with pytest.raises(ValidationError):
            EvidenceCandidate.model_validate(values)
    else:
        values = candidate.locator.model_dump()
        values[field] += "\n"
        with pytest.raises(ValidationError):
            QuoteLocator.model_validate(values)
