#!/usr/bin/env python
"""T6: regenerate RESULTS.md from `runs/`. See `aiv.analysis.results` for the logic.

    uv run python scripts/make_results.py [--partitions main,live,...] [--runs-root runs] \
        [--out RESULTS.md]

Refuses to write RESULTS.md (exits non-zero, prints the faults) if any run it would cite fails
its hash-chain verification (ARCHITECTURE invariant 6).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aiv.analysis.results import VerificationError, run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    from aiv.cli import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # AIV_RECORDER_KEY for live runs
    parser = argparse.ArgumentParser(prog="make_results")
    parser.add_argument("--partitions", default="mock", help="comma-separated partition list")
    parser.add_argument("--runs-root", default="runs")
    parser.add_argument("--out", default="RESULTS.md")
    parser.add_argument("--fig-dir", default="analysis")
    parser.add_argument(
        "--runs",
        default=None,
        help="comma-separated run_id allowlist, to pick one run out of several "
        "duplicate/rerun directories in a partition",
    )
    args = parser.parse_args(argv)

    partitions = [p.strip() for p in args.partitions.split(",") if p.strip()]
    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()] if args.runs else None
    repo_root = Path(__file__).resolve().parent.parent
    try:
        run(
            runs_root=args.runs_root,
            partitions=partitions,
            out_path=args.out,
            repo_root=repo_root,
            fig_dir=args.fig_dir,
            run_ids=run_ids,
        )
    except VerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(
            f"refused: {exc}\nhint: pass --runs <run_id,...> to pick one run per "
            "(seed, condition, state) if runs/ holds duplicates from a re-run batch.",
            file=sys.stderr,
        )
        return 2
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
