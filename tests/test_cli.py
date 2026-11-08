"""T4a: `aiv` CLI. Live mode must refuse without `--confirm-spend`, without `--batch`, and
without a matching authorization file — and must never make a network call. Authorization files
are created only under `tmp_path`, never under `protocol/authorizations/`.
"""

from __future__ import annotations

import json

import pytest

import aiv.cli as cli
from aiv.agents import MockAdapter
from aiv.contract import (
    ApprovalState,
    ApprovalTuple,
    Condition,
    NearMissType,
    Partition,
    Report,
    Task,
    World,
)
from aiv.runtime import EpisodeSpec

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def make_world() -> World:
    report = Report(report_version=2, title="t", body="b")
    task = Task(task_id="T-0000", seed=0, report=report, destination="site-a")
    req = task.requested_tuple
    near_miss = ApprovalTuple(req.task_id, req.report_version - 1, req.destination)
    return World(
        seed=0,
        approval_state=ApprovalState.APPROVED,
        task=task,
        near_miss_type=NearMissType.OLDER_VERSION,
        near_miss_tuple=near_miss,
        approvals=frozenset({req}),
        destinations=("site-a", "site-b"),
    )


def write_auth(tmp_path, batch_id: str, partition: str, **extra) -> None:
    auth_dir = tmp_path / "auths"
    auth_dir.mkdir(exist_ok=True)
    payload = {
        "batch_id": batch_id,
        "partition": partition,
        "max_usd": 5,
        "authorized_by": "sahil",
        "created": "2026-11-07",
        **extra,
    }
    (auth_dir / f"{batch_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return auth_dir


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """Defense in depth: no test in this file may reach the live adapter factory or an
    unfaked `run_batch`. Tests that need to inspect a successful wiring (mock mode) override
    `cli.run_batch` again in their own body, after this fixture has already run."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("live/network path reached in a test")

    monkeypatch.setattr(cli, "make_live_adapter_factory", _boom)
    monkeypatch.setattr(cli, "run_batch", _boom)
    yield


# ---------------------------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------------------------


def test_parse_seeds_ranges_and_lists():
    assert cli._parse_seeds("0-2") == (0, 1, 2)
    assert cli._parse_seeds("0,2,5") == (0, 2, 5)
    assert cli._parse_seeds("0-1,4") == (0, 1, 4)


def test_parse_conditions_and_states():
    assert cli._parse_conditions("ABCD") == (Condition.A, Condition.B, Condition.C, Condition.D)
    assert cli._parse_states("approved,missing") == (ApprovalState.APPROVED, ApprovalState.MISSING)


# ---------------------------------------------------------------------------------------------
# Live gate: refusals (no network call is ever reachable in these tests)
# ---------------------------------------------------------------------------------------------


def test_check_live_gate_refuses_without_confirm_spend(tmp_path):
    err = cli.check_live_gate(
        confirm_spend=False, batch="b1", partition=Partition.LIVE, auth_root=tmp_path
    )
    assert err is not None and "confirm-spend" in err


def test_check_live_gate_refuses_without_batch(tmp_path):
    err = cli.check_live_gate(
        confirm_spend=True, batch=None, partition=Partition.LIVE, auth_root=tmp_path
    )
    assert err is not None and "--batch" in err


def test_check_live_gate_refuses_path_traversal_batch_id(tmp_path):
    err = cli.check_live_gate(
        confirm_spend=True, batch="../evil", partition=Partition.LIVE, auth_root=tmp_path
    )
    assert err is not None


def test_check_live_gate_refuses_missing_authorization_file(tmp_path):
    err = cli.check_live_gate(
        confirm_spend=True, batch="no-such-batch", partition=Partition.LIVE, auth_root=tmp_path
    )
    assert err is not None and "authorization" in err


def test_check_live_gate_refuses_partition_mismatch(tmp_path):
    auth_dir = write_auth(tmp_path, "b1", "discovery")
    err = cli.check_live_gate(
        confirm_spend=True, batch="b1", partition=Partition.LIVE, auth_root=auth_dir
    )
    assert err is not None and "partition" in err


def test_check_live_gate_accepts_matching_authorization(tmp_path):
    auth_dir = write_auth(tmp_path, "b1", "live")
    err = cli.check_live_gate(
        confirm_spend=True, batch="b1", partition=Partition.LIVE, auth_root=auth_dir
    )
    assert err is None


def test_cmd_run_live_refused_without_confirm_spend(capsys):
    code = cli.main(
        ["run", "--mode", "live", "--partition", "live", "--seeds", "0", "--batch", "b1"]
    )
    assert code == 2
    assert "confirm-spend" in capsys.readouterr().err


def test_cmd_run_live_refused_without_batch(capsys):
    code = cli.main(
        ["run", "--mode", "live", "--partition", "live", "--seeds", "0", "--confirm-spend"]
    )
    assert code == 2
    assert "--batch" in capsys.readouterr().err


def test_cmd_run_live_refused_without_authorization_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(tmp_path / "auths"))
    code = cli.main(
        [
            "run",
            "--mode",
            "live",
            "--partition",
            "live",
            "--seeds",
            "0",
            "--confirm-spend",
            "--batch",
            "b1",
        ]
    )
    assert code == 2
    assert "authorization" in capsys.readouterr().err


def test_cmd_run_live_refused_without_actor_model_id(tmp_path, monkeypatch, capsys):
    auth_dir = write_auth(tmp_path, "b1", "live")
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(auth_dir))
    monkeypatch.delenv("ACTOR_MODEL_ID", raising=False)
    code = cli.main(
        [
            "run",
            "--mode",
            "live",
            "--partition",
            "live",
            "--seeds",
            "0",
            "--confirm-spend",
            "--batch",
            "b1",
        ]
    )
    assert code == 2
    assert "ACTOR_MODEL_ID" in capsys.readouterr().err


def test_cmd_run_live_refused_when_ledger_unavailable(tmp_path, monkeypatch, capsys):
    # Force the "ledger unavailable" branch explicitly, so this test still exercises it once
    # T4b lands `aiv.spend.Ledger` (today it would also refuse via ImportError, but that would
    # silently stop testing this branch rather than the real spend code, which must never be
    # exercised here — no paid call may ever become reachable from a test).
    auth_dir = write_auth(tmp_path, "b1", "live")
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(auth_dir))
    monkeypatch.setenv("ACTOR_MODEL_ID", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("AIV_SPEND_ROOT", str(tmp_path / "spend"))
    monkeypatch.setattr(cli, "_build_live_ledger", lambda partition: None)
    code = cli.main(
        [
            "run",
            "--mode",
            "live",
            "--partition",
            "live",
            "--seeds",
            "0",
            "--confirm-spend",
            "--batch",
            "b1",
        ]
    )
    assert code == 2
    assert "ledger is unavailable" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# freeze hook
# ---------------------------------------------------------------------------------------------


def test_freeze_subcommand_delegates_to_freeze_module(tmp_path, capsys):
    draft = tmp_path / "protocol" / "draft"
    draft.mkdir(parents=True)
    (draft / "p.md").write_text("prompt\n", encoding="utf-8")
    code = cli.main(["freeze", "--repo-root", str(tmp_path), "create"])
    assert code == 0
    freeze_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert (tmp_path / "protocol" / "frozen" / freeze_id / "p.md").exists()
    assert cli.main(["freeze", "--repo-root", str(tmp_path), "validate", freeze_id]) == 0


# ---------------------------------------------------------------------------------------------
# Mock run wiring (run_batch is faked out: no world/service/handoff modules exist yet)
# ---------------------------------------------------------------------------------------------


def test_cmd_run_mock_wires_config_and_adapter_factory(monkeypatch):
    captured: dict = {}

    def fake_run_batch(config, deps, adapter_factory, *, run_id=None, ledger=None, workers=1):
        del deps, run_id, ledger
        captured["config"] = config
        captured["adapter_factory"] = adapter_factory
        captured["workers"] = workers
        return {"run_id": "r1", "run_dir": "runs/mock/r1", "n_episodes": 4}

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)

    code = cli.main(
        [
            "run",
            "--mode",
            "mock",
            "--partition",
            "mock",
            "--seeds",
            "0-1",
            "--conditions",
            "AB",
            "--states",
            "approved,missing",
            "--mock-policy",
            "diligent",
            "--workers",
            "3",
        ]
    )

    assert code == 0
    cfg = captured["config"]
    assert cfg.seeds == (0, 1)
    assert cfg.conditions == (Condition.A, Condition.B)
    assert cfg.approval_states == (ApprovalState.APPROVED, ApprovalState.MISSING)
    assert captured["workers"] == 3

    world = make_world()
    spec = EpisodeSpec(
        seed=0,
        condition=Condition.A,
        approval_state=ApprovalState.APPROVED,
        wording_variant=0,
        attempt=0,
        episode_id="x",
        chain=False,
    )
    adapter = captured["adapter_factory"](spec, world)
    assert isinstance(adapter, MockAdapter)
    assert adapter.policy == "diligent"


def test_cmd_run_mock_mixed_policy_assigns_by_seed_and_condition(monkeypatch):
    captured: dict = {}

    def fake_run_batch(config, deps, adapter_factory, *, run_id=None, ledger=None, workers=1):
        del config, deps, run_id, ledger, workers
        captured["adapter_factory"] = adapter_factory
        return {"run_id": "r1", "run_dir": "runs/mock/r1", "n_episodes": 1}

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)

    code = cli.main(
        [
            "run",
            "--mode",
            "mock",
            "--partition",
            "mock",
            "--seeds",
            "0-1",
            "--conditions",
            "ABCD",
            "--mock-policy",
            "mixed",
        ]
    )
    assert code == 0

    world = make_world()
    spec0 = EpisodeSpec(0, Condition.A, ApprovalState.APPROVED, 0, 0, "x", False)
    spec1 = EpisodeSpec(0, Condition.A, ApprovalState.MISSING, 0, 0, "y", False)
    adapter0 = captured["adapter_factory"](spec0, world)
    adapter1 = captured["adapter_factory"](spec1, world)
    # same (seed, condition) -> same policy regardless of approval_state
    assert adapter0.policy == adapter1.policy


# ---------------------------------------------------------------------------------------------
# Lead integration: spend root, freeze requirement, chain mode
# ---------------------------------------------------------------------------------------------

LIVE_ARGS = ["run", "--mode", "live", "--seeds", "0", "--confirm-spend", "--batch", "b1"]


def test_live_refused_without_shared_spend_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(write_auth(tmp_path, "b1", "pilot")))
    monkeypatch.setenv("ACTOR_MODEL_ID", "m")
    monkeypatch.delenv("AIV_SPEND_ROOT", raising=False)
    assert cli.main([*LIVE_ARGS, "--partition", "pilot"]) == 2
    assert "AIV_SPEND_ROOT" in capsys.readouterr().err


def test_live_main_refused_without_freeze_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(write_auth(tmp_path, "b1", "main")))
    monkeypatch.setenv("ACTOR_MODEL_ID", "m")
    monkeypatch.setenv("AIV_SPEND_ROOT", str(tmp_path / "spend"))
    monkeypatch.setattr(cli, "_build_live_ledger", lambda partition: object())
    monkeypatch.setattr(cli, "make_live_adapter_factory", _no_live)
    assert cli.main([*LIVE_ARGS, "--partition", "main"]) == 2
    assert "require --freeze-id" in capsys.readouterr().err


def _no_live(*a, **k):
    raise AssertionError("a live client must never be built in tests")


def test_unknown_freeze_id_refused(capsys):
    code = cli.main(["run", "--partition", "mock", "--seeds", "0", "--freeze-id", "F-nope"])
    assert code == 2
    assert "run refused" in capsys.readouterr().err


def test_freeze_mismatch_detected(tmp_path):
    from aiv import freeze

    draft = tmp_path / "protocol" / "draft"
    draft.mkdir(parents=True)
    (draft / "p.md").write_text("v1\n", encoding="utf-8")
    fid = freeze.create(tmp_path, {})
    assert cli.check_frozen_matches_draft(tmp_path, fid) is None
    (draft / "p.md").write_text("v2\n", encoding="utf-8")
    assert "differs" in cli.check_frozen_matches_draft(tmp_path, fid)


def test_chain_rejects_b_d(capsys):
    code = cli.main(
        ["run", "--partition", "mock", "--seeds", "0", "--chain", "ordinary", "--conditions", "AB"]
    )
    assert code == 2


@pytest.mark.parametrize("variant", ["ordinary", "adversarial"])
def test_mock_chain_run_end_to_end(tmp_path, monkeypatch, variant):
    from aiv.contract import EventType, RunConfig
    from aiv.recorder import read_events, recorder_key, verify_run
    from aiv.runtime import run_batch
    from aiv.scoring import read_scored

    # Mock mode only: the real batch runner is safe (no client is ever constructed).
    monkeypatch.setattr(cli, "run_batch", run_batch)
    monkeypatch.setattr(cli, "RunConfig", lambda **kw: RunConfig(**kw, runs_root=str(tmp_path)))
    code = cli.main(["run", "--partition", "mock", "--seeds", "0-1", "--chain", variant])
    assert code == 0
    (run_dir,) = (tmp_path / "mock").iterdir()
    assert verify_run(run_dir, recorder_key("mock")[0]) == []
    scored = read_scored(run_dir)
    assert len(scored) == 2 * 2 * 2 and all(s.chain for s in scored)
    events = read_events(run_dir)
    workers = [
        e.payload["text"]
        for e in events
        if e.type == EventType.AGENT_MESSAGE and e.payload["role"] == "worker"
    ]
    adversarial = variant == "adversarial"
    assert workers and all(("approved" in text) == adversarial for text in workers)
    starts = [e for e in events if e.type == EventType.EPISODE_START]
    assert {e.payload["chain_variant"] for e in starts} == {variant}
