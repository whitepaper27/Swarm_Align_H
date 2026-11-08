"""T14 trace tool: renders only verified, self-consistent runs."""

from __future__ import annotations

import json

import pytest

from aiv.agents import MockAdapter
from aiv.analysis.trace import (
    TraceError,
    check_consistent,
    load_verified,
    render_episode,
    review_queue,
)
from aiv.contract import ApprovalState, Condition, RunConfig, TerminalOutcome
from aiv.runtime import Deps, run_batch


@pytest.fixture
def run_dir(tmp_path):
    config = RunConfig(
        seeds=(0, 1),
        conditions=(Condition.A, Condition.B),
        approval_states=(ApprovalState.MISSING,),
        runs_root=str(tmp_path),
    )

    def factory(spec, world):
        policy = "ignore_denial" if spec.condition == Condition.B else "diligent"
        return MockAdapter(policy, world)

    run_batch(config, Deps(), factory, run_id="trace-run")
    return tmp_path / "mock" / "trace-run"


def test_review_queue_and_render(run_dir):
    events, scored = load_verified(run_dir)
    check_consistent(events, scored)
    queue = review_queue(scored)
    assert queue and all(
        s.terminal_outcome == TerminalOutcome.UNAUTHORIZED_PUBLICATION or s.narrative_vs_record_flag
        for s in queue
    )
    s = queue[0]
    md = render_episode(events[s.episode_id], s, "mock/trace-run")
    assert f"Trace `{s.episode_id}`" in md
    assert "category **exact_denial**" in md
    assert "**committed**" in md
    assert "supersedes version" in md or "also prepared for" in md  # handoff text included


def test_tampered_run_refused(run_dir):
    p = run_dir / "events.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace("not_approved", "approved", 1), "utf-8")
    with pytest.raises(TraceError):
        load_verified(run_dir)


def test_stale_scores_detected(run_dir):
    events, scored = load_verified(run_dir)
    path = run_dir / "scored.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["terminal_outcome"] = "safe_return" if rows[0]["committed_tuple"] else "refusal"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    _, scored = load_verified(run_dir)
    with pytest.raises(TraceError):
        check_consistent(events, scored)


def test_cli_list_and_render(run_dir, tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "render_trace", Path(__file__).resolve().parents[1] / "scripts" / "render_trace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([str(run_dir), "--list"]) == 0
    assert "need manual review" in capsys.readouterr().out
    out = tmp_path / "review.md"
    assert mod.main([str(run_dir), "--review", "--out", str(out)]) == 0
    assert "### Trace" in out.read_text(encoding="utf-8")
