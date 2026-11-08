"""Render annotated traces from a verified run (T14).

  uv run python scripts/render_trace.py runs/main/<run_id> --list
      list the manual-review queue: every unauthorized publication and narrative flag
  uv run python scripts/render_trace.py runs/main/<run_id> --episode <id> [--out docs/trace.md]
      render one episode
  uv run python scripts/render_trace.py runs/main/<run_id> --review [--out docs/review_queue.md]
      render every episode in the review queue

The run's hash chain is verified first, and every scored record is checked against a rescoring
of its events, so a trace can only show what the run files contain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aiv.analysis.trace import (  # noqa: E402
    TraceError,
    check_consistent,
    load_verified,
    render_episode,
    review_queue,
)


def main(argv: list[str] | None = None) -> int:
    from aiv.cli import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # AIV_RECORDER_KEY for live runs
    ap = argparse.ArgumentParser(prog="render_trace")
    ap.add_argument("run_dir")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--episode")
    mode.add_argument("--review", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    try:
        events, scored = load_verified(run_dir)
        check_consistent(events, scored)
    except TraceError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    ref = f"{run_dir.parent.name}/{run_dir.name}"
    by_id = {s.episode_id: s for s in scored}

    if args.list:
        queue = review_queue(scored)
        print(f"{len(queue)} episodes need manual review in {ref}")
        for s in queue:
            why = [
                n
                for n, on in (
                    ("unauthorized", s.unauthorized_commit),
                    ("narrative-flag", s.narrative_vs_record_flag),
                )
                if on
            ]
            cats = ",".join(a.category.value for a in s.attempts) or "-"
            print(f"  {s.episode_id}  {'+'.join(why)}  categories={cats}")
        return 0

    if args.episode:
        if args.episode not in by_id:
            print(f"no episode {args.episode!r} in {ref}", file=sys.stderr)
            return 2
        targets = [by_id[args.episode]]
    else:
        targets = review_queue(scored)
    text = "\n".join(render_episode(events[s.episode_id], s, ref) for s in targets)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out} ({len(targets)} episodes)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
