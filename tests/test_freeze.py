"""Invariant 7 companion (ARCHITECTURE §11/§12): freeze create/validate roundtrip, tamper
detection, missing/extra-file detection, and refusal to overwrite an existing freeze. Everything
here uses `tmp_path` as a fake repo root; the real `protocol/frozen` is never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aiv import freeze


def make_draft(repo_root):
    draft = repo_root / "protocol" / "draft"
    (draft / "prompts").mkdir(parents=True)
    (draft / "prompts" / "a.txt").write_text("hello world\n", encoding="utf-8")
    (draft / "config.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    return draft


FIXED_NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


def test_create_and_validate_roundtrip(tmp_path):
    make_draft(tmp_path)
    config = {"n_seeds": 4, "conditions": ["A", "B"]}
    freeze_id = freeze.create(tmp_path, config, note="unit test", now=FIXED_NOW)

    assert freeze_id.startswith("F20261108-0900-")
    faults = freeze.validate(tmp_path, freeze_id)
    assert faults == []

    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    assert (frozen_dir / "prompts" / "a.txt").read_text(encoding="utf-8") == "hello world\n"
    assert json.loads((frozen_dir / "config.json").read_text(encoding="utf-8")) == {"x": 1}

    record = json.loads((frozen_dir / "freeze.json").read_text(encoding="utf-8"))
    assert record["freeze_id"] == freeze_id
    assert record["config"] == config
    assert record["note"] == "unit test"
    assert set(record["files"]) == {"prompts/a.txt", "config.json"}


def test_create_with_default_config_has_contrasts_and_stopping_rule(tmp_path):
    make_draft(tmp_path)
    freeze_id = freeze.create(tmp_path, freeze.DEFAULT_CONFIG, now=FIXED_NOW)
    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    record = json.loads((frozen_dir / "freeze.json").read_text(encoding="utf-8"))
    assert "primary_contrasts" in record["config"]
    assert "stopping_rule" in record["config"]


def test_validate_missing_freeze_reports_fault(tmp_path):
    make_draft(tmp_path)
    faults = freeze.validate(tmp_path, "F20260101-0000-deadbeef")
    assert faults
    assert any("freeze_missing" in f for f in faults)


def test_tampered_file_detected(tmp_path):
    make_draft(tmp_path)
    freeze_id = freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)
    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    (frozen_dir / "prompts" / "a.txt").write_text("tampered content\n", encoding="utf-8")

    faults = freeze.validate(tmp_path, freeze_id)
    assert faults
    assert any("hash_mismatch: prompts/a.txt" in f for f in faults)


def test_missing_file_detected(tmp_path):
    make_draft(tmp_path)
    freeze_id = freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)
    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    (frozen_dir / "prompts" / "a.txt").unlink()

    faults = freeze.validate(tmp_path, freeze_id)
    assert faults
    assert any("missing_file: prompts/a.txt" in f for f in faults)


def test_extra_file_detected(tmp_path):
    make_draft(tmp_path)
    freeze_id = freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)
    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    (frozen_dir / "surprise.txt").write_text("not frozen by create()\n", encoding="utf-8")

    faults = freeze.validate(tmp_path, freeze_id)
    assert faults
    assert any("extra_file: surprise.txt" in f for f in faults)


def test_edited_manifest_hash_detected(tmp_path):
    # Editing the recorded hash (or the config) inside freeze.json itself, without touching
    # the file on disk, must also be caught via the content-hash / freeze_id-suffix check.
    make_draft(tmp_path)
    freeze_id = freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)
    frozen_dir = tmp_path / "protocol" / "frozen" / freeze_id
    freeze_path = frozen_dir / "freeze.json"
    record = json.loads(freeze_path.read_text(encoding="utf-8"))
    record["files"]["prompts/a.txt"] = "0" * 64
    freeze_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    faults = freeze.validate(tmp_path, freeze_id)
    assert faults
    assert any("content_hash" in f or "freeze_id suffix" in f for f in faults)


def test_refuses_to_overwrite_existing_freeze(tmp_path):
    make_draft(tmp_path)
    freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)
    with pytest.raises(FileExistsError):
        freeze.create(tmp_path, {"k": "v"}, now=FIXED_NOW)  # same files+config+minute => same id


def test_cli_create_and_validate(tmp_path, capsys):
    make_draft(tmp_path)
    config_path = tmp_path / "cfg.json"
    config_path.write_text(json.dumps({"n_seeds": 2}), encoding="utf-8")

    rc = freeze.main(["--repo-root", str(tmp_path), "create", "--config", str(config_path)])
    assert rc == 0
    freeze_id = capsys.readouterr().out.strip()
    assert freeze_id.startswith("F")

    rc = freeze.main(["--repo-root", str(tmp_path), "validate", freeze_id])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "OK"


def test_cli_validate_reports_faults_and_nonzero_exit(tmp_path, capsys):
    make_draft(tmp_path)
    rc = freeze.main(["--repo-root", str(tmp_path), "validate", "F20260101-0000-deadbeef"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "freeze_missing" in out
