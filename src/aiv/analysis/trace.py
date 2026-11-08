"""Annotated episode traces (T14), rendered from verified run files only.

`render_episode` turns one episode's service-ordered events into a Markdown trace: each lookup is
marked exact / non-matching against the requested tuple, each publication attempt carries its
scored verification category, and the header repeats the scored record. Everything shown is read
from the run files; nothing is typed by hand. `review_queue` lists the episodes sol.md §10 says
must be inspected manually: every unauthorized publication and every narrative-vs-record flag.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from aiv.contract import Event, EventType, ScoredEpisode, TerminalOutcome
from aiv.recorder import (
    events_by_episode,
    read_events,
    recorder_key,
    verify_mode_for,
    verify_run,
)
from aiv.scoring import read_scored, score_episode

_MAX_TEXT = 600


class TraceError(RuntimeError):
    pass


def _key_for(run_dir: Path) -> bytes:
    return recorder_key(verify_mode_for(run_dir.parent.name, run_dir.name))[0]


def load_verified(run_dir: str | os.PathLike[str]) -> tuple[dict[str, list[Event]], list]:
    """(events by episode, scored records) for a run whose hash chain verifies."""
    run_dir = Path(run_dir)
    faults = verify_run(run_dir, _key_for(run_dir))
    if faults:
        raise TraceError(f"{run_dir} does not verify: {faults[:3]}")
    return events_by_episode(read_events(run_dir)), read_scored(run_dir)


def _tuple(d: dict) -> str:
    return f"({d['task_id']}, v{d['report_version']}, {d['destination']})"


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + " …"


def _row(e: Event, requested: dict, categories: dict[str, str]) -> tuple[str, str]:
    p = e.payload
    t = e.type
    if t == EventType.EPISODE_START:
        return (
            f"condition {p['condition']}, state {p['approval_state']}, near miss "
            f"{p['near_miss_type']}, requested {_tuple(p['requested_tuple'])}",
            "",
        )
    if t == EventType.AGENT_MESSAGE:
        return f"**{p['role']}:** {_clip(p['text'])}", ""
    if t == EventType.HANDOFF_DELIVERED:
        return f"handoff delivered to publisher ({len(p['text'])} chars, see below)", ""
    if t == EventType.MODEL_CALL:
        return (
            f"{p['role']} model call → {p['returned_model']}, stop `{p['stop_reason']}`, "
            f"{p['input_tokens']}+{p['output_tokens']} tokens",
            "",
        )
    if t == EventType.REGISTRY_LOOKUP:
        exact = p["tuple"] == requested
        note = "exact tuple" if exact else "**non-matching tuple**"
        return f"lookup {_tuple(p['tuple'])} → `{p['response']['status']}`", note
    if t == EventType.PUBLICATION_ATTEMPT:
        cat = categories.get(p["attempt_id"], "?")
        sub = "" if p["tuple"] == requested else "; **substituted tuple**"
        return (
            f"publish attempt {p['attempt_id']} {_tuple(p['tuple'])}",
            f"category **{cat}**; registry: `{p['registry_status']}`{sub}",
        )
    if t == EventType.PUBLICATION_COMMIT:
        return f"**committed** {_tuple(p['tuple'])}", f"registry: `{p['registry_status']}`"
    if t == EventType.PUBLICATION_REJECTED:
        return f"rejected {_tuple(p['tuple'])}: {p['reason']}", ""
    if t == EventType.PUBLISHER_RETURN_DRAFT:
        return f"returned draft: {_clip(p['note'])}", ""
    if t == EventType.PUBLISHER_REQUEST_APPROVAL:
        return f"requested approval for {_tuple(p['tuple'])}: {_clip(p['note'])}", ""
    if t == EventType.EPISODE_END:
        return f"episode end: `{p['reason']}` ({p['tool_calls']} tool calls)", ""
    return f"{t.value}: {_clip(p)}", ""


def render_episode(events: Sequence[Event], scored: ScoredEpisode, run_ref: str) -> str:
    evs = sorted(events, key=lambda e: e.seq)
    requested = scored.requested_tuple
    categories = {a.attempt_id: a.category.value for a in scored.attempts}
    handoff = next((e.payload["text"] for e in evs if e.type == EventType.HANDOFF_DELIVERED), "")
    flags = [
        name
        for name, on in (
            ("unauthorized commit", scored.unauthorized_commit),
            ("tuple substitution", scored.tuple_substitution),
            ("procedural noncompliance", scored.procedural_noncompliance),
            ("narrative vs record", scored.narrative_vs_record_flag),
        )
        if on
    ]
    lines = [
        f"### Trace `{scored.episode_id}`",
        "",
        f"Run: `{run_ref}` · seed {scored.seed} · condition {scored.condition.value} · "
        f"state {scored.approval_state.value} · chain {scored.chain} · "
        f"model {scored.requested_model} → {', '.join(scored.returned_models) or '—'}",
        "",
        f"**Terminal outcome:** `{scored.terminal_outcome.value}` · exact verification before "
        f"decision: {scored.exact_verified_before_terminal} · flags: {', '.join(flags) or 'none'}",
        "",
        "| seq | actor | event | annotation |",
        "|---|---|---|---|",
    ]
    for e in evs:
        text, note = _row(e, requested, categories)
        text = text.replace("|", "\\|")
        lines.append(f"| {e.seq} | {e.actor.value} | {text} | {note} |")
    lines += ["", "<details><summary>Exact handoff text the publisher saw</summary>", "", "```"]
    lines += [handoff, "```", "", "</details>", ""]
    return "\n".join(lines)


def review_queue(scored: Sequence[ScoredEpisode]) -> list[ScoredEpisode]:
    """Episodes that sol.md §10 requires to be inspected by hand, in a stable order."""
    return sorted(
        (
            s
            for s in scored
            if s.terminal_outcome == TerminalOutcome.UNAUTHORIZED_PUBLICATION
            or s.narrative_vs_record_flag
        ),
        key=lambda s: s.episode_id,
    )


def check_consistent(events: dict[str, list[Event]], scored: Sequence[ScoredEpisode]) -> None:
    """Rescoring from events must reproduce every stored record (scoring is a pure function)."""
    for s in scored:
        if score_episode(events[s.episode_id], infra_retries=s.infra_retries) != s:
            raise TraceError(f"scored.jsonl disagrees with events for {s.episode_id}")
