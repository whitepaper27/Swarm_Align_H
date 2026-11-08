"""RESULTS.md section 10 (cross-provider strata, sol.md §16) on real, hash-chained mock runs.

- Two publisher models on the same seeds in one x-partition never collide (strata by model).
- Sections 1-9 are byte-identical whether or not x-partitions are passed.
- Absent x-partitions read NOT RUN; a tampered x run is refused; mock x runs are labeled MOCK.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aiv.agents import MockAdapter
from aiv.analysis.results import VerificationError, render
from aiv.cli import make_chain_handoff
from aiv.contract import ApprovalState, Condition, Mode, Partition, RunConfig
from aiv.recorder import verify_mode_for
from aiv.runtime import CONFIRM_EXPERIMENT, Deps, run_batch

MISSING = (ApprovalState.MISSING,)
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_results.py"


def _run(runs_root, partition, policy, *, conditions=tuple(Condition), extra=None, chain=False,
         deps=None, seeds=(0, 1, 2)):  # fmt: skip
    config = RunConfig(
        mode=Mode.MOCK,
        partition=partition,
        seeds=seeds,
        conditions=conditions,
        approval_states=MISSING,
        runs_root=str(runs_root),
        extra=extra or {},
        chain=chain,
    )
    return run_batch(config, deps or Deps(), lambda spec, world: MockAdapter(policy, world))


def _mock_roles(world, episode_id):
    from aiv.cli import _mock_role_adapters

    return _mock_role_adapters(world, True)


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    _run(root, Partition.MOCK, "skip_check")  # core (sections 1-5)
    confirm = {"experiment": CONFIRM_EXPERIMENT}
    ab = (Condition.A, Condition.B)
    _run(root, Partition.CONFIRM, "diligent", conditions=ab, extra=confirm)
    # two "models" (mock policies report requested_model mock-<policy>) on the same seeds
    _run(root, Partition.XMAIN, "skip_check")
    _run(root, Partition.XMAIN, "diligent")
    _run(root, Partition.XCONFIRM, "ignore_denial", conditions=ab, extra=confirm)
    _run(root, Partition.XCONFIRM, "diligent", conditions=ab, extra=confirm)
    for worker_model in ("gem-worker", "gpt-worker"):  # two worker models on the same seeds
        deps = Deps()
        deps.build_handoff = make_chain_handoff(adversarial=True, role_factory=_mock_roles)
        _run(
            root,
            Partition.XLIVE,
            "skip_check",
            conditions=(Condition.A, Condition.C),
            chain=True,
            deps=deps,
            extra={"chain_variant": "adversarial", "worker_model": worker_model},
        )
    _run(root, Partition.XDISCOVERY, "near_miss_check", seeds=(0,))
    return root


BASE = ["mock", "confirm", "live"]
CROSS = ["xmain", "xconfirm", "xlive", "xdiscovery"]


def test_sections_1_to_9_byte_identical_with_or_without_x(runs, tmp_path):
    without, _ = render(runs, BASE, repo_root=tmp_path, n_boot=200)
    with_x, _ = render(runs, BASE + CROSS, repo_root=tmp_path, n_boot=200)
    assert "## 10." not in without
    assert with_x.startswith(without)
    assert with_x[len(without) :].lstrip().startswith("## 10. Cross-provider")


def test_section10_strata_by_model(runs, tmp_path):
    text, _ = render(runs, BASE + CROSS, repo_root=tmp_path, n_boot=200)
    s10 = text[text.index("## 10.") :]
    xmain = s10[s10.index("### 10a") : s10.index("### 10b")]
    assert "**Publisher `mock-diligent`**" in xmain and "**Publisher `mock-skip_check`**" in xmain
    skip = xmain[xmain.index("mock-skip_check") :]
    # skip_check publishes without any lookup: every missing episode is an unauthorized commit
    assert "| A | 3/3, CP" in skip and "| no_lookup |" not in skip
    assert "| A | 3/3, CP [0.292, 1.000] | 3 | 0 | 0 | 0 |" in skip
    dil = xmain[xmain.index("mock-diligent") : xmain.index("mock-skip_check")]
    assert "| A | 0/3 observed, upper bound" in dil
    assert "B − A (assertion) | +0/−0 | 3 |" in dil
    xconfirm = s10[s10.index("### 10b") : s10.index("### 10c")]
    assert "**Publisher `mock-diligent`**" in xconfirm
    assert "**Publisher `mock-ignore_denial`**" in xconfirm
    assert "Renewed exact verification" in xconfirm
    xlive = s10[s10.index("### 10c") : s10.index("### 10d")]
    assert "| `gem-worker` | adversarial | `mock-skip_check` | 6 | 6/6 |" in xlive
    assert "| `gpt-worker` | adversarial | `mock-skip_check` | 6 | 6/6 |" in xlive
    assert "NOT RUN (labels 0/6)" in xlive
    assert "`mock-near_miss_check`" in s10[s10.index("### 10d") :]
    assert s10.count("MOCK — not a result") >= 4


def test_xlive_labels_are_read_when_present(runs, tmp_path):
    from aiv.analysis import tables

    eps = tables.load_partition_episodes(runs, "xlive")
    labels = tmp_path / "docs" / "labels"
    labels.mkdir(parents=True)
    rows = [
        {"episode_id": e.episode_id, "origin": "worker_originated", "worker_refused": False,
         "publisher_adopted_claim": True}
        for e in eps
    ]  # fmt: skip
    path = labels / "xlive_labels_final.jsonl"
    # Without run_id, the two worker models' episodes (same seeds -> same episode ids) are
    # ambiguous: no label is applied.
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    text, _ = render(runs, ["xlive"], repo_root=tmp_path, n_boot=200)
    assert text.count("NOT RUN (labels 0/6)") == 6
    keyed = [{**r, "run_id": e.run_id} for r, e in zip(rows, eps, strict=True)]
    path.write_text("\n".join(json.dumps(r) for r in keyed) + "\n", encoding="utf-8")
    text, _ = render(runs, ["xlive"], repo_root=tmp_path, n_boot=200)
    assert text.count("| 6 / 0 / 0 / 0 | 0/6 | 6/6 |") == 2


def test_absent_x_partitions_read_not_run(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    text, _ = render(root, ["mock", *CROSS], repo_root=tmp_path, n_boot=200)
    s10 = text[text.index("## 10.") :]
    for sub in ("### 10a", "### 10b", "### 10d"):
        assert s10[s10.index(sub) :].split("\n\n")[1].startswith("NOT RUN")
    assert "| NOT RUN | NOT RUN | NOT RUN |" in s10


def test_tampered_x_run_is_refused(runs, tmp_path):
    run_dir = next((runs / "xmain").iterdir())
    events = run_dir / "events.jsonl"
    events.write_text(
        events.read_text(encoding="utf-8").replace("not_approved", "approved"),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(VerificationError):
        render(runs, ["xmain"], repo_root=tmp_path, n_boot=200)


def test_live_x_runs_verify_with_the_live_key():
    for p in CROSS:
        assert verify_mode_for(p, "live-xmain-20261108T000000Z-abc") == Mode.LIVE
        assert verify_mode_for(p, "mock-xmain-20261108T000000Z-abc") == Mode.MOCK
    assert verify_mode_for("main", "mock-main-x") == Mode.LIVE  # unchanged


def test_make_results_script_with_x_partitions(runs, tmp_path):
    spec = importlib.util.spec_from_file_location("aiv_make_results_cli_x", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "RESULTS.md"
    code = module.main(
        [
            "--partitions", "main,live,confirm,xmain,xconfirm,xlive",
            "--runs-root", str(runs), "--out", str(out), "--fig-dir", str(tmp_path / "fig"),
        ]
    )  # fmt: skip
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "## 10. Cross-provider" in text and "**Publisher `mock-diligent`**" in text
