"""Rebuild `scored.jsonl` from a run's events with the current scoring code.

  uv run python scripts/rescore.py runs/<partition>/<run_id> [...]

`scored.jsonl` is derived data: scoring is a pure function of the hash-chained `events.jsonl`,
which is never touched. Use this after a scoring change (recorded in docs/decisions.md) so that
traces and RESULTS.md agree with the events. Infra-retry counts are recomputed from the attempts
in the run (episode ids `...-a<k>`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aiv.analysis.trace import TraceError, load_verified  # noqa: E402
from aiv.contract import SCORED_FILE, canonical_json  # noqa: E402
from aiv.scoring import score_episode  # noqa: E402


def rescore(run_dir: Path) -> int:
    events, _old = load_verified(run_dir)  # refuses a run whose chain does not verify
    finals: dict[str, tuple[int, str]] = {}
    for eid in events:
        base, _, attempt = eid.rpartition("-a")
        k = int(attempt)
        if base not in finals or k > finals[base][0]:
            finals[base] = (k, eid)
    rows = []
    for _base, (k, eid) in sorted(finals.items()):
        evs = events[eid]
        if not any(e.type == "episode.end" for e in evs):
            continue  # an episode cut off by a batch abort is not scored (coverage shows it)
        rows.append(score_episode(evs, infra_retries=k))
    rows.sort(key=lambda s: s.episode_id)
    with open(run_dir / SCORED_FILE, "w", encoding="utf-8", newline="\n") as f:
        for s in rows:
            f.write(canonical_json(s.to_dict()) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    from aiv.cli import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    status = 0
    for arg in argv if argv is not None else sys.argv[1:]:
        try:
            print(f"{arg}: rescored {rescore(Path(arg))} episodes")
        except TraceError as exc:
            print(f"{arg}: refused ({exc})", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
