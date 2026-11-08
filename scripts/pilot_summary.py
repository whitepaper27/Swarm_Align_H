"""Write docs/pilot.md from pilot/discovery runs and the spend ledger (T9). Generated, not typed.

  uv run python scripts/pilot_summary.py [--partitions pilot,discovery] [--n-main 20] [--out F]

Reports, per run: episodes, terminal outcomes, verification categories of committing attempts,
tokens, and ledger spend; the mean cost per controlled episode; the projected cost of the main
evaluation (N seeds x 4 conditions x 2 states) against SPEND_CAP_MAIN_USD; and the observed
denial->publish base rate in A and B. Exploratory only: these runs are never pooled into the
confirmatory estimates. The section 9 gate answers are left for the owner.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aiv.analysis.trace import TraceError, check_consistent, load_verified  # noqa: E402
from aiv.contract import Category, Condition  # noqa: E402

MICRO = Decimal(1_000_000)


def _ledger_episodes() -> dict:
    root = Path(os.environ.get("AIV_SPEND_ROOT") or ROOT / ".spend")
    path = root / "ledger.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("episodes", {})


def _cost(ledger_eps: dict, run_id: str, episode_id: str) -> Decimal | None:
    entry = ledger_eps.get(f"{run_id}/{episode_id}") or ledger_eps.get(episode_id)
    return None if entry is None else Decimal(entry["spent_micro"]) / MICRO


def main(argv: list[str] | None = None) -> int:
    from aiv.cli import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="pilot_summary")
    ap.add_argument("--partitions", default="pilot,discovery")
    ap.add_argument("--runs-root", default=str(ROOT / "runs"))
    ap.add_argument("--n-main", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "docs" / "pilot.md"))
    args = ap.parse_args(argv)

    ledger_eps = _ledger_episodes()
    lines = [
        "# Pilot and discovery summary (T9)",
        "",
        "> **GENERATED** by `scripts/pilot_summary.py` from run files and the spend ledger. "
        "**Exploratory**: never pooled into confirmatory estimates.",
        "",
    ]
    ctl_costs: list[Decimal] = []
    denial_publish = collections.Counter()
    missing_ab = collections.Counter()
    for part in [p for p in args.partitions.split(",") if p]:
        pdir = Path(args.runs_root) / part
        if not pdir.exists():
            lines += [f"## {part}", "", "NOT RUN", ""]
            continue
        lines += [f"## {part}", ""]
        for run_dir in sorted(
            d for d in pdir.iterdir() if d.is_dir() and not d.name.startswith("_")
        ):
            try:
                events, scored = load_verified(run_dir)
                check_consistent(events, scored)
            except TraceError as exc:
                lines += [f"### `{run_dir.name}`: REFUSED ({exc})", ""]
                continue
            outcomes = collections.Counter(s.terminal_outcome.value for s in scored)
            cats = collections.Counter(a.category.value for s in scored for a in s.attempts)
            costs = [_cost(ledger_eps, s.run_id, s.episode_id) for s in scored]
            known = [c for c in costs if c is not None]
            chain = sorted({s.chain for s in scored})
            variant = {
                e.payload.get("chain_variant")
                for evs in events.values()
                for e in evs
                if e.type == "episode.start"
            }
            lines += [
                f"### `{part}/{run_dir.name}`",
                "",
                f"- episodes: {len(scored)} · chain: {chain} · "
                f"chain variant: {sorted(map(str, variant))}",
                f"- outcomes: {dict(outcomes)}",
                f"- attempt categories: {dict(cats)}",
                f"- tokens in/out: {sum(s.input_tokens for s in scored)}"
                f"/{sum(s.output_tokens for s in scored)}",
                f"- ledger spend: ${sum(known, Decimal(0)):.4f} over {len(known)}/{len(scored)} "
                "episodes with a ledger entry",
                "",
            ]
            for s, c in zip(scored, costs, strict=True):
                if not s.chain and c is not None:
                    ctl_costs.append(c)
                controlled_ab = not s.chain and s.condition in (Condition.A, Condition.B)
                if controlled_ab and s.approval_state.value == "missing":
                    missing_ab[s.condition.value] += 1
                    if s.unauthorized_commit and any(
                        a.category == Category.EXACT_DENIAL for a in s.attempts
                    ):
                        denial_publish[s.condition.value] += 1

    cap = os.environ.get("SPEND_CAP_MAIN_USD")
    lines += ["## Projection for the main evaluation", ""]
    if ctl_costs:
        mean = sum(ctl_costs, Decimal(0)) / len(ctl_costs)
        n_eps = args.n_main * 4 * 2
        proj = mean * n_eps
        lines += [
            f"- mean ledger cost per controlled episode: ${mean:.4f} (n = {len(ctl_costs)})",
            f"- projected main cost: {args.n_main} seeds × 4 × 2 = {n_eps} episodes → ${proj:.2f}",
            f"- SPEND_CAP_MAIN_USD: {cap or 'unset'}"
            + (
                f" → {'fits' if proj <= Decimal(cap) else 'EXCEEDS: reduce N before freeze'}"
                if cap
                else ""
            ),
        ]
    else:
        lines += ["- no controlled episodes with ledger entries yet"]
    lines += [
        "",
        "## Denial→publish base rate (controlled, missing state)",
        "",
    ]
    for c in ("A", "B"):
        lines.append(f"- condition {c}: {denial_publish[c]}/{missing_ab[c]}")
    lines += [
        "",
        "## Competitive-result gate (GOAL §9): owner answers",
        "",
        "1. Service-recorded violation or clearly measured verification change? _owner_",
        "2. Failure category unambiguous? _owner_",
        "3. Feasible fresh comparison supporting the explanation? _owner_",
        "4. Distinct from the closest verified prior work (docs/contribution.md)? _owner_",
        "",
        "If A and B both show 0 denial→publish events, the pre-registered follow-up is the T13 "
        "injected-denial design. Do not tune prompts.",
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
