"""`aiv` command line. Owned by T4 (runtime); `run` and `freeze` subcommands land here.

Live mode is refused unless `--confirm-spend` *and* `--batch <id>` are given *and*
`protocol/authorizations/<batch>.json` exists with a matching partition (docs/decisions.md
"Spend + live gate"). The gate runs before any run directory or client is constructed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from aiv.agents import AnthropicAdapter, MockAdapter, mixed_policy_for
from aiv.contract import ApprovalState, Condition, Mode, Partition, RunConfig
from aiv.runtime import Deps, EpisodeSpec, run_batch

AUTH_ROOT_ENV = "AIV_AUTH_ROOT"
DEFAULT_AUTH_ROOT = "protocol/authorizations"


def _parse_seeds(spec: str) -> tuple[int, ...]:
    seeds: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            seeds.extend(range(int(a), int(b) + 1))
        else:
            seeds.append(int(part))
    return tuple(seeds)


def _parse_conditions(spec: str) -> tuple[Condition, ...]:
    return tuple(Condition(c) for c in spec)


def _parse_states(spec: str) -> tuple[ApprovalState, ...]:
    return tuple(ApprovalState(s.strip()) for s in spec.split(",") if s.strip())


def _valid_batch_id(batch: str) -> bool:
    return bool(batch) and batch not in (".", "..") and not any(c in batch for c in "/\\")


def check_live_gate(
    *,
    confirm_spend: bool,
    batch: str | None,
    partition: Partition,
    auth_root: str | os.PathLike[str] | None = None,
) -> str | None:
    """Return an error message if live mode should be refused, else `None`. Runs before any
    client, ledger or run directory is created."""
    if not confirm_spend:
        return "live mode refused: pass --confirm-spend to make paid calls"
    if not batch or not _valid_batch_id(batch):
        return "live mode refused: --batch <authorized-batch-id> is required"
    root = Path(
        auth_root if auth_root is not None else os.environ.get(AUTH_ROOT_ENV, DEFAULT_AUTH_ROOT)
    )
    auth_path = root / f"{batch}.json"
    if not auth_path.exists():
        return f"live mode refused: no authorization file for batch {batch!r} ({auth_path})"
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"live mode refused: could not read authorization file: {exc}"
    if auth.get("partition") != partition.value:
        return (
            f"live mode refused: authorization partition {auth.get('partition')!r} "
            f"!= requested partition {partition.value!r}"
        )
    return None


def make_mock_adapter_factory(
    policy: str, *, seeds: tuple[int, ...], conditions: tuple[Condition, ...]
):
    if policy != "mixed" and policy not in MockAdapter.POLICIES:
        raise ValueError(f"unknown mock policy {policy!r}")

    def factory(spec: EpisodeSpec, world):
        name = policy
        if policy == "mixed":
            name = mixed_policy_for(spec.seed, spec.condition, seeds=seeds, conditions=conditions)
        return MockAdapter(name, world)

    return factory


def make_live_adapter_factory(model_id: str, ledger: object | None):
    import anthropic

    # max_retries=0: the SDK's own retry-on-429/5xx would bypass the ledger and the frozen
    # retry policy (ARCHITECTURE §10/§11) — every retry must go through our InfraError path.
    client = anthropic.Anthropic(max_retries=0)

    def factory(spec: EpisodeSpec, world):
        del world
        return AnthropicAdapter(client, model_id, episode_id=spec.episode_id, ledger=ledger)

    return factory


def _build_live_ledger(partition: Partition):
    try:
        from aiv.spend import Ledger
    except ImportError:
        return None
    return Ledger.from_env(partition.value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiv")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run a batch of episodes")
    run_p.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.MOCK.value)
    run_p.add_argument("--partition", required=True)
    run_p.add_argument("--seeds", required=True, help="e.g. 0-1 or 0,2,5")
    run_p.add_argument("--conditions", default=None, help="e.g. ABCD (default: all)")
    run_p.add_argument("--states", default=None, help="e.g. approved,missing (default: both)")
    run_p.add_argument(
        "--mock-policy",
        default="diligent",
        help="mock policy name, or 'mixed' to cycle every policy by (seed, condition)",
    )
    run_p.add_argument("--workers", type=int, default=1)
    run_p.add_argument("--confirm-spend", action="store_true")
    run_p.add_argument("--batch", default=None)

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    mode = Mode(args.mode)
    try:
        partition = Partition(args.partition)
    except ValueError:
        print(f"unknown partition {args.partition!r}", file=sys.stderr)
        return 2
    seeds = _parse_seeds(args.seeds)
    conditions = _parse_conditions(args.conditions) if args.conditions else RunConfig().conditions
    states = _parse_states(args.states) if args.states else RunConfig().approval_states

    config = RunConfig(
        mode=mode,
        partition=partition,
        seeds=seeds,
        conditions=conditions,
        approval_states=states,
    )

    deps = Deps()

    if mode == Mode.MOCK:
        adapter_factory = make_mock_adapter_factory(
            args.mock_policy, seeds=seeds, conditions=conditions
        )
        result = run_batch(config, deps, adapter_factory, workers=args.workers)
    else:
        gate_error = check_live_gate(
            confirm_spend=args.confirm_spend, batch=args.batch, partition=partition
        )
        if gate_error:
            print(gate_error, file=sys.stderr)
            return 2
        model_id = os.environ.get("ACTOR_MODEL_ID")
        if not model_id:
            print("live mode refused: ACTOR_MODEL_ID is not set", file=sys.stderr)
            return 2
        ledger = _build_live_ledger(partition)
        if ledger is None:
            print("live mode refused: aiv.spend ledger is unavailable", file=sys.stderr)
            return 2
        config = replace(config, model_id=model_id)
        adapter_factory = make_live_adapter_factory(model_id, ledger)
        result = run_batch(config, deps, adapter_factory, ledger=ledger, workers=args.workers)

    print(f"run_id={result['run_id']} run_dir={result['run_dir']} episodes={result['n_episodes']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["freeze"]:
        try:
            from aiv.freeze import main as freeze_main
        except ImportError:
            print("freeze not available")
            return 1
        return freeze_main(argv[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "run":
        return _cmd_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
