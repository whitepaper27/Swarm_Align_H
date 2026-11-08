"""Real `aiv.spend.Ledger` wired through the live adapter, runtime and chain (no network).

A spend refusal must end the episode as `budget_exhausted` (never retried, never an API call),
on the publisher path and on the chain-role path; an aborted batch must keep what finished.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aiv.agents import AnthropicAdapter, MockAdapter
from aiv.cli import make_chain_handoff
from aiv.contract import (
    ApprovalState,
    Condition,
    EventType,
    Mode,
    Partition,
    RunConfig,
    TerminalOutcome,
)
from aiv.recorder import recorder_key, verify_run
from aiv.runtime import Deps, run_batch, run_episode
from aiv.scoring import read_scored
from aiv.service import ListSink
from aiv.spend import Ledger


class _Client:
    """Fake Anthropic client: records calls, returns a plain end_turn text response."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            model="m-returned",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


def _ledger(tmp_path, episode_cap="0.000001"):
    return Ledger(
        tmp_path / "spend",
        "pilot",
        price_in_per_mtok="1",
        price_out_per_mtok="5",
        total_cap="50",
        partition_cap="5",
        episode_cap=episode_cap,
    )


CONFIG = RunConfig(mode=Mode.LIVE, partition=Partition.PILOT, model_id="m", retry_max=2)


def _run(deps, factory, ledger):
    return run_episode(
        seed=0,
        condition=Condition.A,
        approval_state=ApprovalState.MISSING,
        wording_variant=0,
        config=CONFIG,
        deps=deps,
        adapter_factory=factory,
        sink=ListSink(),
        run_id="r",
        ledger=ledger,
    )


def test_publisher_spend_refusal_is_budget_exhausted(tmp_path):
    client, ledger = _Client(), _ledger(tmp_path)
    factory = lambda spec, world: AnthropicAdapter(  # noqa: E731
        client, "m", episode_id=spec.episode_id, ledger=ledger
    )
    result = _run(Deps(), factory, ledger)
    assert result.scored.terminal_outcome == TerminalOutcome.BUDGET_EXHAUSTED
    assert len(result.attempts) == 1 and client.calls == []
    kinds = [e.payload["kind"] for e in result.attempts[0].events if e.type == "budget.exhausted"]
    assert kinds == ["spend"]


def test_chain_role_spend_refusal_is_budget_exhausted(tmp_path):
    client, ledger = _Client(), _ledger(tmp_path)

    def roles(world, episode_id):
        return tuple(
            AnthropicAdapter(client, "m", episode_id=episode_id, ledger=ledger) for _ in "wc"
        )

    deps = Deps(build_handoff=make_chain_handoff(adversarial=False, role_factory=roles))
    factory = lambda spec, world: MockAdapter("diligent", world)  # noqa: E731
    result = _run(deps, factory, ledger)
    assert result.scored.terminal_outcome == TerminalOutcome.BUDGET_EXHAUSTED
    assert len(result.attempts) == 1 and client.calls == []
    types = [e.type for e in result.attempts[0].events]
    assert EventType.BUDGET_EXHAUSTED in types and types[-1] == EventType.EPISODE_END


def test_ledger_settles_real_calls(tmp_path):
    client, ledger = _Client(), _ledger(tmp_path, episode_cap="0.80")
    factory = lambda spec, world: AnthropicAdapter(  # noqa: E731
        client, "m", episode_id=spec.episode_id, ledger=ledger
    )
    result = _run(Deps(), factory, ledger)
    assert result.scored.terminal_outcome == TerminalOutcome.REFUSAL  # end_turn, no tool
    assert len(client.calls) == 1
    state = json.loads((tmp_path / "spend" / "ledger.json").read_text(encoding="utf-8"))
    assert state["totals"]["spent_micro"] > 0


class _Boom(MockAdapter):
    def complete(self, **kwargs):
        raise ValueError("non-retryable config error")


@pytest.mark.parametrize("workers", [1, 2])
def test_aborted_batch_keeps_completed_episodes(tmp_path, workers):
    config = RunConfig(
        seeds=(0, 1, 2),
        conditions=(Condition.A,),
        approval_states=(ApprovalState.MISSING,),
        runs_root=str(tmp_path),
    )

    def factory(spec, world):
        return (_Boom if spec.seed == 1 else MockAdapter)("diligent", world)

    with pytest.raises(ValueError):
        run_batch(config, Deps(), factory, run_id=f"abort-{workers}", workers=workers)
    run_dir = tmp_path / "mock" / f"abort-{workers}"
    assert (run_dir / "manifest.json").exists()
    assert verify_run(run_dir, recorder_key("mock")[0]) == []  # closed with a checkpoint
    seeds = {s.seed for s in read_scored(run_dir)}
    assert 1 not in seeds and 0 in seeds
