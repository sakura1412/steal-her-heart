"""Run the bundled exporters against a portable editorial plan."""
from pathlib import Path
import json
import shutil
import sys

import pytest
import reportlab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-skills/steal-her-heart/scripts"))
from export_report import export
from report_text import check_text_map, pages
from run_files import digest, validate

FONT = Path(reportlab.__file__).parent / "fonts/Vera.ttf"


@pytest.mark.parametrize("fmt", ["pdf", "pptx"])
def test_standalone_export(tmp_path, fmt):
    root = shutil.copytree(ROOT / "examples/rental-quick", tmp_path / "run")
    plan = {"presentation_copy": True, "input_manifest_sha256": digest(root / "quick.json"),
            "sections": [{"title": "Weekly rental comparison", "blocks": [
                {"kind": "analysis", "text": "The prices match on day five.",
                 "claim_refs": [{"record_id": "break-even", "revision": 1}]}]}]}
    output = root / "reports" / ("fresh." + fmt)
    draft = json.loads(export(root, plan, output, FONT).read_text())
    validate("ledger-record", draft)
    check_text_map(output, draft["data"])
    assert draft["data"]["status"] == "draft"
    assert "The prices match on day five." in pages(output, fmt)[0]
    with pytest.raises(FileExistsError):
        export(root, plan, output, FONT)
    plan["input_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="snapshot"):
        export(root, plan, output, FONT)
