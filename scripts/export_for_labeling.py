"""Export episodes for labeling from verified run files (T12 live labels, T14 flag review).

  uv run python scripts/export_for_labeling.py

Writes:
- docs/labels/live_episodes.md: every live-chain episode (worker, coordinator, publisher messages,
  registry lookups, terminal action), for the false-approval-claim labels of sol.md §5;
- docs/labels/flagged_episodes.md: every episode with narrative_vs_record_flag in the main, live and
  confirm partitions, for review of whether the flag is a real contradiction.

Only text from service-recorded events appears. Approval state and registry responses are included
because a claim is false only relative to the record.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aiv.analysis.trace import check_consistent, load_verified  # noqa: E402


def _tuple(d: dict) -> str:
    return f"({d['task_id']}, v{d['report_version']}, {d['destination']})"


def _episode_md(events: list, scored) -> str:
    start = events[0].payload
    lines = [
        f"### {scored.episode_id}",
        "",
        f"- run: `{scored.run_id}` · approval state: **{scored.approval_state.value}** · condition "
        f"{scored.condition.value} · chain variant: {start.get('chain_variant')} · arm: "
        f"{start.get('arm')}",
        f"- requested tuple: {_tuple(scored.requested_tuple)} · terminal outcome: "
        f"`{scored.terminal_outcome.value}`",
        "",
    ]
    for e in events:
        p = e.payload
        if e.type == "agent.message":
            text = str(p["text"]).strip().replace("\n", "\n> ")
            lines += [f"**seq {e.seq} · {p['role']}:**", "", f"> {text}", ""]
        elif e.type == "registry.lookup":
            status = p["response"]["status"]
            lines.append(f"- seq {e.seq} · registry lookup {_tuple(p['tuple'])} → `{status}`")
        elif e.type in (
            "publication.commit",
            "publisher.return_draft",
            "publisher.request_approval",
        ):
            lines.append(f"- seq {e.seq} · `{e.type}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    from aiv.cli import load_dotenv

    load_dotenv(ROOT / ".env")  # AIV_RECORDER_KEY verifies live run files
    out_dir = ROOT / "docs" / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)
    live, flagged = [], []
    for part in ("main", "live", "confirm"):
        for run_dir in sorted((ROOT / "runs" / part).glob("*/")):
            events, scored = load_verified(run_dir)
            check_consistent(events, scored)
            for s in sorted(scored, key=lambda s: s.episode_id):
                md = _episode_md(sorted(events[s.episode_id], key=lambda e: e.seq), s)
                if part == "live":
                    live.append(md)
                if s.narrative_vs_record_flag:
                    flagged.append(md)
    header = "> Exported by `scripts/export_for_labeling.py` from hash-verified run files.\n\n"
    (out_dir / "live_episodes.md").write_text(
        "# Live-chain episodes for labeling\n\n" + header + "\n".join(live), "utf-8"
    )
    (out_dir / "flagged_episodes.md").write_text(
        "# Narrative-flagged episodes for review\n\n" + header + "\n".join(flagged), "utf-8"
    )
    print(f"live episodes: {len(live)} · flagged episodes: {len(flagged)} → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
