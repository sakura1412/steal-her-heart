"""Preset diagnostics are explicit and local; they cannot start competitor research."""

import json
import os
import socket
from pathlib import Path

import pytest

from steal_her_heart.cli import main
from steal_her_heart.research_presets import load_research_preset


def test_preset_cli_works_without_project_network_or_state(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("No network expected"))
    assert main(["--project-name", "missing", "preset", "saas"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "research_preset" and payload["status"] == "ok"
    assert payload["data"]["preset"]["execution"] == "configuration_only"
    assert payload["data"]["research_ready"] is False
    assert payload["data"]["review_assessment"] is None
    assert len(payload["data"]["preset"]["dimensions"]) == 25
    assert not list(tmp_path.iterdir())


def test_runtime_preset_mirror_is_identical_to_packaged_single_source():
    repository = Path(__file__).resolve().parents[1]
    package = repository / "src/steal_her_heart/presets/saas.json"
    runtime = repository / "codex-skills/steal-her-heart/references/saas.json"
    assert package.read_bytes() == runtime.read_bytes()
    assert load_research_preset().template.id == "saas"


def test_unknown_or_incomplete_options_do_not_read_file(tmp_path, capsys):
    absent = tmp_path / "absent.json"
    with pytest.raises(SystemExit) as caught:
        main(["preset", "unknown"])
    assert caught.value.code == 2
    capsys.readouterr()
    assert main(["preset", "saas", "--review-sample", str(absent)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "PRESET_INPUT_INVALID"
    assert not absent.exists()
    assert main(["preset", "saas", "--as-of", "2026-11-04"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "PRESET_INPUT_INVALID"


def test_empty_declared_sample_reports_explicit_coverage_gaps_and_no_pass(tmp_path, capsys):
    sample = tmp_path / "reviews.json"
    sample.write_text('{"records":[],"platform_attempts":[]}')
    before = sample.read_bytes()
    assert main(["preset", "saas", "--review-sample", str(sample), "--as-of", "2026-11-04"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assessment = payload["data"]["review_assessment"]
    assert assessment["coverage_complete"] is False and assessment["facts_validated"] is False
    assert {"platform_not_checked", "rating_band_shortfall"} <= {
        issue["code"] for issue in assessment["issues"]
    }
    assert sample.read_bytes() == before
    assert list(tmp_path.iterdir()) == [sample]


@pytest.mark.parametrize(
    "body",
    [
        b"invalid",
        b"x" * 1048577,
        b'{"records":[{"id":"x"}]}',
        b'{"records":[],"platform_attempts":[],"records":[]}',
        b'{"records":[],"platform_attempts":[{"outcome":"unavailable","outcome":"checked"}]}',
    ],
)
def test_invalid_or_oversized_sample_returns_safe_json_error(tmp_path, capsys, body):
    sample = tmp_path / "bad.json"
    sample.write_bytes(body)
    assert main(["preset", "saas", "--review-sample", str(sample), "--as-of", "2026-11-04"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "PRESET_INPUT_INVALID"
    assert "bad.json" not in result["error"]["message"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO available only on POSIX")
def test_non_regular_sample_is_rejected_without_blocking(tmp_path, capsys):
    sample = tmp_path / "reviews.fifo"
    os.mkfifo(sample)
    assert main(["preset", "saas", "--review-sample", str(sample), "--as-of", "2026-11-04"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "PRESET_INPUT_INVALID"


def test_non_saas_command_does_not_load_preset(tmp_path, monkeypatch, capsys):
    import steal_her_heart.review_sampling as sampling

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sampling, "load_research_preset", lambda *a: pytest.fail("No implicit preset")
    )
    assert main(["/analyze", "本地餐饮品牌"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["execution"] == "pending_module"
    assert not list(tmp_path.iterdir())
