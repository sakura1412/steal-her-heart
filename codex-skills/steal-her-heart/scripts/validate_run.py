"""Check the locked snapshot and report maps before delivery. Exit 1 on failure."""
import argparse
import sys
from pathlib import Path

from report_text import check_text_map
from run_files import checked_path, digest, load, read_full, read_quick, require, validate

COUNTERS = ("research_operations", "document_versions", "supplement_rounds",
            "report_repair_rounds", "global_rework_rounds")
CAPS = {"Quick": (12, 6, 1, 1, 2), "Standard": (40, 20, 2, 2, 4), "Deep": (100, 50, 3, 3, 6)}
TYPES = {
    "source_ref": "source", "supporting_evidence": "evidence", "counter_evidence": "evidence",
    "opposing_evidence": "evidence", "evidence_ref": "evidence", "evidence_refs": "evidence",
    "resolution_evidence": "evidence", "reusable_evidence": "evidence", "claim_refs": "claim",
    "affected_claims": "claim", "claims_to_recheck": "claim", "conflict_refs": "conflict",
    "entity_refs": "entity", "from_entity": "entity", "to_entity": "entity",
    "superseded_by": "entity", "dependencies": "task",
}


def key(ref):
    return ref["record_id"], ref["revision"]


def references(node, field=""):
    if isinstance(node, dict):
        if set(node) in ({"record_id", "revision"}, {"question_id", "revision"}):
            yield field, node
        else:
            for name, value in node.items():
                yield from references(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from references(value, field)


def check_design(framework, questions, records, events):
    question_keys = {(q["question_id"], q["revision"]) for q in questions}
    require(len(question_keys) == len(questions), "duplicate question revision")
    categories = {c["category_id"] for c in framework.get("business_categories", [])}
    dims = {d["dimension_id"] for d in framework.get("dimensions", [])}
    for dim in framework.get("dimensions", []):
        require(set(dim["category_ids"]) <= categories, "unknown category ID")
        require(set(dim["depends_on_dimensions"]) <= dims, "unknown dimension dependency")
    for q in questions:
        require(not q["parent_question_id"] or any(q["parent_question_id"] == i for i, _ in question_keys), "parent question ID")
        require(set(q["counter_search"]["operation_keys"]) <= events, "unknown counter-search operation")
    if framework:
        for item in records:
            dim = item["data"].get("dimension_id")
            require(not dim or dim in dims, f"unknown dimension: {dim}")
    return question_keys


def check_run(directory, artifact=None, legacy_map=False):
    root = Path(directory).resolve()
    snapshots = [p for p in (root / "manifest.json", root / "quick.json") if p.exists()]
    require(len(snapshots) == 1, "expected exactly one manifest.json or quick.json")
    snapshot = snapshots[0]
    require(snapshot.resolve().parent == root, "snapshot outside run")
    run = load(snapshot)
    quick = snapshot.name == "quick.json"
    records, questions, backgrounds, framework = read_quick(run) if quick else read_full(root, run)
    mapping = (root / "reports/reports.jsonl").resolve()
    require(root in mapping.parents, "report map outside run")
    reports = load(mapping)
    require(reports, "missing report map")
    for report in reports:
        validate("ledger-record", report)
        require(report["record_type"] == "report", "expected report record")
    rows = records + reports
    index = {key(row): row for row in rows}
    require(len(index) == len(rows), "duplicate record ID/revision")
    background_ids = {b["background_id"] for b in backgrounds}
    require(len(background_ids) == len(backgrounds), "duplicate background ID")
    events = [r["data"] for r in records if r["record_type"] == "event"]
    question_keys = check_design(framework, questions, records, {e["operation_key"] for e in events})
    for row in [framework, *questions, *rows]:
        previous = row.get("supersedes")
        if previous:
            id_field = "record_id" if "record_id" in row else "question_id"
            require(previous[id_field] == row[id_field] and previous["revision"] < row["revision"], "invalid supersedes")
        for field, target in references(row):
            if "question_id" in target:
                require((target["question_id"], target["revision"]) in question_keys, f"unknown question: {target}")
                continue
            record = index.get(key(target))
            expected = row.get("record_type") if field == "supersedes" else TYPES.get(field)
            require(record and (not expected or record["record_type"] == expected), f"invalid {field}: {target}")

    for row in records:
        data = row["data"]
        if row["record_type"] == "source":
            require(bool(data.get("snapshot_path")) == bool(data.get("snapshot_sha256")), "incomplete source hash")
            if data.get("snapshot_path"):
                checked_path(root, data["snapshot_path"], data["snapshot_sha256"])
        if row["record_type"] == "evidence" and not quick:
            source = index[key(data["source_ref"])]["data"]
            require(all(data[a] == source[b] for a, b in (("source_url", "url"), ("collected_at", "collected_at"),
                                                            ("source_type", "source_type"))), "evidence/source mismatch")

    budget = run if quick else run["budget"]
    settled = [e["operation_key"] for e in events if any(e.get(c, 0) for c in COUNTERS)]
    require(len(settled) == len(set(settled)), "operation charged more than once")
    source_versions = {(r["data"]["url"], r["data"].get("snapshot_sha256")) for r in records
                       if r["record_type"] == "source" and r["data"].get("snapshot_sha256")}
    require(len(source_versions) <= budget["used"]["document_versions"], "unaccounted source versions")
    for counter, cap in zip(COUNTERS, CAPS[run["mode"]]):
        used = budget["used"][counter]
        require(used == sum(event.get(counter, 0) for event in events), f"event total: {counter}")
        limit = budget.get("limits", {}).get(counter, cap)
        require(used + budget.get("reserved", {}).get(counter, 0) <= limit <= cap, f"budget limit: {counter}")

    # A later failed revision cannot authorize delivery via an earlier successful one.
    current = {}
    for row in reports:
        identifier = row["record_id"]
        if identifier not in current or row["revision"] > current[identifier]["revision"]:
            current[identifier] = row
    passed = 0
    for report in current.values():
        data = report["data"]
        if artifact and (not data["artifact_path"] or (root / data["artifact_path"]).resolve() != (root / artifact).resolve()):
            continue
        require(data["status"] == "qa_passed", "requested artifact has not passed QA")
        passed += 1
        require(data["input_snapshot_id"] == run["snapshot_id"], "report snapshot ID mismatch")
        require(data["input_manifest_sha256"] == digest(snapshot), "report snapshot hash mismatch")
        path = checked_path(root, data["artifact_path"], data["artifact_sha256"])
        if report["schema_version"] == "1.2.0":
            check_text_map(path, data)
        else:
            require(legacy_map, "legacy report map: text coverage unavailable")
        require(all(data["checks"][c] == "passed" for c in ("content", "render", "citations")), "unfinished report checks")
        facts = [p for p in data["paragraphs"] if p["kind"] in ("fact", "analysis")]
        status = run["quality"] if quick else run["qa"]["status"]
        require(facts or (status == "insufficient" and any(p["kind"] == "limitation" for p in data["paragraphs"])),
                "missing report conclusions")
        for p in data["paragraphs"]:
            require(set(p.get("background_refs", [])) <= background_ids, "unknown background ID")
            require(p["kind"] not in ("fact", "analysis") or p["claim_refs"], f"missing claim: {p['location']}")
            for target in p["claim_refs"]:
                claim = index[key(target)]["data"]
                require(claim["status"] == "supported" and claim["supporting_evidence"], f"unsupported claim: {target}")
                for ref in claim["supporting_evidence"]:
                    require(index[key(ref)]["data"]["verification_status"] == "verified", f"unverified evidence: {ref}")
    require(passed, "no QA-passed artifact")
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="directory containing the locked snapshot and reports/")
    parser.add_argument("--artifact", help="check this output path relative to the run")
    parser.add_argument("--legacy-map", action="store_true", help="audit historical maps without text ranges")
    args = parser.parse_args()
    try:
        count = check_run(args.run, args.artifact, args.legacy_map)
        print(f"{'LEGACY AUDIT' if args.legacy_map else 'PASS'}: {count} report(s)")
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"FAIL: {error}")
