"""Exercise publication gates on copies of the two distributable teaching runs."""
import json
from pathlib import Path
import shutil
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "codex-skills/steal-her-heart"
sys.path.insert(0, str(SKILL / "scripts"))
from run_files import digest, load, validator
from validate_run import check_run


@pytest.fixture
def run(tmp_path):
    return shutil.copytree(ROOT / "examples/rental-quick", tmp_path / "run")


def save(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def mutate(path, field_path, value):
    data = json.loads(path.read_text())
    obj = data
    for key in field_path[:-1]:
        obj = obj[key]
    obj[field_path[-1]] = value
    save(path, data)


@pytest.mark.parametrize("name", ["rental-quick", "rental-limited"])
def test_deliverable_examples(name):
    assert check_run(ROOT / "examples" / name, "report.pdf") == 1


@pytest.mark.parametrize(("keys", "value", "error"), [
    (("sources", 0, "collected_at"), "yesterday", "date-time|quick-run"),
    (("sources", 0, "id"), "SB", "duplicate"),
    (("evidence", 0, "source_id"), "missing", "invalid source_ref"),
    (("claims", 0, "supporting_ids"), ["SA"], "invalid supporting_evidence"),
    (("claims", 0, "supporting_ids"), [], "unsupported claim"),
    (("evidence", 0, "verification_status"), "stale", "unverified"),
    (("used", "research_operations"), 1, "event total"),
    (("quality",), "limited", "gaps"),
    (("mode",), "Deep", "quick-run"),
    (("sources", 0, "snapshot_path"), "../outside", "outside run"),
])
def test_broken_snapshot(run, keys, value, error):
    mutate(run / "quick.json", keys, value)
    mutate(run / "reports/reports.jsonl", ("data", "input_manifest_sha256"), digest(run / "quick.json"))
    with pytest.raises(ValueError, match=error):
        check_run(run)


@pytest.mark.parametrize(("keys", "value", "error"), [
    (("paragraphs", 1, "claim_refs"), [], "missing claim"),
    (("paragraphs", 1, "claim_refs", 0, "record_id"), "EA", "invalid claim_refs"),
    (("paragraphs", 1, "claim_refs", 0, "revision"), 2, "invalid claim_refs"),
    (("paragraphs",), [], "conclusions|constraint|unmapped"),
    (("input_snapshot_id",), "another-run", "snapshot ID"),
    (("input_manifest_sha256",), "1" * 64, "snapshot hash"),
    (("checks", "render"), "failed", "report checks|constraint"),
])
def test_broken_map(run, keys, value, error):
    mutate(run / "reports/reports.jsonl", ("data", *keys), value)
    with pytest.raises(ValueError, match=error):
        check_run(run)


@pytest.mark.parametrize("file", ["report.pdf", "raw/A.txt"])
def test_artifact_tampered(run, file):
    with (run / file).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        check_run(run)


def test_quick_cannot_raise_own_cap(run):
    data = load(run / "quick.json")
    data["used"]["research_operations"] = 13
    data["limits"] = dict(data["used"])
    data["events"][0]["counts"]["research_operations"] = 13
    save(run / "quick.json", data)
    with pytest.raises(ValueError, match="budget limit"):
        check_run(run)


def test_undeclared_conclusion(run):
    path = run / "reports/reports.jsonl"
    row = load(path)[0]
    del row["data"]["paragraphs"][1]
    save(path, row)
    with pytest.raises(ValueError, match="unmapped"):
        check_run(run)


def test_overlapping_blocks(run):
    mutate(run / "reports/reports.jsonl", ("data", "paragraphs", 0, "text_ranges"), [[1, 1, 3]])
    with pytest.raises(ValueError, match="overlapping"):
        check_run(run)


def test_old_report_can_only_be_audited(run):
    path = run / "reports/reports.jsonl"
    row = load(path)[0]
    row["schema_version"] = "1.1.0"
    for p in row["data"]["paragraphs"]:
        del p["text_ranges"]
    save(path, row)
    with pytest.raises(ValueError, match="legacy report map"):
        check_run(run)
    assert check_run(run, legacy_map=True) == 1


def test_unlisted_artifact(run):
    with pytest.raises(ValueError, match="no QA-passed artifact"):
        check_run(run, "another.pdf")


@pytest.mark.parametrize(("method_id", "required"), [
    ("rice", {"own_reach_by_initiative", "own_reach_source", "own_effort_person_months", "own_effort_source"}),
    ("kano", {"paired_survey_responses", "respondent_feature_pair_keys", "survey_sample_and_window"}),
    ("bcg_matrix", {"unit_and_largest_rival_shares", "common_market_denominator", "matched_geography_period_and_measure"}),
])
def test_method_input_contract(method_id, required):
    registry = yaml.safe_load((SKILL / "references/analysis-method-registry.yaml").read_text())
    method = next(m for m in registry["methods"] if m["method_id"] == method_id)
    assert method["on_missing_required_input"] == "blocked"
    assert required <= set(method["required_inputs"])


@pytest.mark.parametrize("path", [SKILL / "schemas/validation-cases.json",
                                  SKILL / "tests/workflow-schema-cases.json",
                                  SKILL / "tests/research-design-schema-cases.json"])
def test_existing_schema_cases(path):
    suite = load(path)
    for case in suite["cases"]:
        name = Path(case.get("schema") or suite["schema"]).name.split(".schema")[0]
        check = validator(name)
        assert check.is_valid(case["instance"]) == case["valid"], case["case_id"]
        if "Intake" in json.dumps(case["instance"]):
            legacy = json.loads(json.dumps(case["instance"]).replace("Intake", "Interview"))
            assert check.is_valid(legacy) == case["valid"], case["case_id"]


def test_native_slide_order_and_markdown_lines(tmp_path):
    from zipfile import ZipFile
    from report_text import check_text_map, pages

    md = tmp_path / "report.md"
    md.write_text("# Summary\n\nA claim.\n", encoding="utf-8")
    check_text_map(md, {"format": "markdown", "paragraphs": [{"text_ranges": [[1, 1, 2]]}]})

    slide = tmp_path / "report.pptx"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with ZipFile(slide, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{p}" xmlns:r="{r}">'
                   '<p:sldIdLst><p:sldId r:id="b"/><p:sldId r:id="a"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="a" Target="slides/slide1.xml"/>'
                   '<Relationship Id="b" Target="slides/slide2.xml"/></Relationships>')
        for number in (1, 2):
            z.writestr(f"ppt/slides/slide{number}.xml", f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><a:t>{number}</a:t></p:sld>')
    assert pages(slide, "pptx") == [["2"], ["1"]]
