"""Read immutable run files and adapt both snapshot formats to one reference graph."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
# These optional format checkers must actually be available.
import rfc3339_validator
import rfc3986_validator


def require(condition, message):
    if not condition:
        raise ValueError(message)


def distinct_pairs(pairs):
    result = dict(pairs)
    require(len(result) == len(pairs), "duplicate JSON key")
    return result


def load(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line, object_pairs_hook=distinct_pairs) for line in text.splitlines()]
    return json.loads(text, object_pairs_hook=distinct_pairs)


@lru_cache()
def validator(name, kind=None):
    schemas = Path(__file__).resolve().parents[1] / "schemas"
    schema = load(schemas / (name + ".schema.json"))
    if kind in schema.get("$defs", {}) and kind != "record_ref":
        schema = {"$ref": f"#/$defs/{kind}", "$defs": schema["$defs"]}
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name, record):
    kind = record.get("record_type") if name == "ledger-record" and isinstance(record, dict) else None
    error = next(validator(name, kind).iter_errors(record), None)
    if error:
        where = ".".join(map(str, error.absolute_path))
        raise ValueError(f"{name} {where}: {error.validator} constraint failed")


def checked_path(root, name, expected):
    path = (root / name).resolve()
    require(root in path.parents, f"path outside run: {name}")
    require(digest(path) == expected, f"SHA-256 mismatch: {name}")
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_full(root, manifest):
    validate("research-manifest", manifest)
    records, questions, backgrounds, framework = [], [], [], {}
    files = {"entities": "entity", "relationships": "relationship", "sources": "source",
             "evidence": "evidence", "claims": "claim", "conflicts": "conflict",
             "coverage": "coverage", "tasks": "task", "events": "event"}
    for name, spec in manifest["ledger_files"].items():
        batch = load(checked_path(root, name, spec["sha256"]))
        require(len(batch) == spec["record_count"], f"record count: {name}")
        for record in batch:
            validate("ledger-record", record)
            require(record["record_type"] == files[Path(name).stem], f"wrong file: {name}")
        records.extend(batch)
    design = manifest.get("research_design") or {}
    for kind, spec, schema in [("framework", design.get("framework"), "research-framework"),
                               ("questions", design.get("questions"), "research-question"),
                               ("background", manifest.get("background_file"), "background-record")]:
        if not spec:
            continue
        value = load(checked_path(root, spec["path"], spec["sha256"]))
        if kind == "framework":
            validate(schema, value)
            require(all(value[k] == spec[k] for k in ("framework_id", "revision")), "framework ID")
            require(value["scope_version"] == design["scope_version"] == manifest["scope_version"], "scope version")
            framework = value
        else:
            require(len(value) == spec["record_count"], f"record count: {kind}")
            for item in value:
                validate(schema, item)
            (questions if kind == "questions" else backgrounds).extend(value)
    return records, questions, backgrounds, framework


def read_quick(run):
    validate("quick-run", run)
    require((run["quality"] == "sufficient") == (not run["gaps"]), "quality and required-unit gaps disagree")
    records = []

    def add(kind, identifier, data):
        records.append(dict(record_type=kind, record_id=identifier, revision=1, data=data))

    for src in run["sources"]:
        add("source", src["id"], src)
    for evidence in run["evidence"]:
        add("evidence", evidence["id"], dict(evidence, source_ref=ref(evidence["source_id"])))
    for claim in run["claims"]:
        add("claim", claim["id"], dict(claim, supporting_evidence=[ref(i) for i in claim["supporting_ids"]]))
    for event in run["events"]:
        add("event", event["key"], dict(event["counts"], operation_key=event["key"]))
    return records, [], [], {}


def ref(identifier):
    return {"record_id": identifier, "revision": 1}
