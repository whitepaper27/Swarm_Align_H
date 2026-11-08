"""T6: scripts/make_results.py / aiv.analysis.results.

Invariant 6: every non-`NOT RUN` row in a generated RESULTS.md cites a run dir that exists and
whose hash chain verifies; a tampered run is refused (no file is written at all).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from aiv.analysis.results import VerificationError, run
from aiv.contract import (
    Actor,
    ApprovalState,
    ApprovalTuple,
    AttemptRecord,
    Category,
    Condition,
    Event,
    EventType,
    NearMissType,
    ScoredEpisode,
    TerminalOutcome,
)
from aiv.recorder import JsonlRecorder, new_run_dir, recorder_key, verify_run

KNOWN_PARTITIONS = ("mock", "pilot", "discovery", "main", "live", "confirm")
CITATION_RE = re.compile(r"`(" + "|".join(KNOWN_PARTITIONS) + r")/([a-zA-Z0-9_.-]+)`")
SEPARATOR_RE = re.compile(r"^\|[-| ]+\|$")
CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_results.py"


def _load_cli():
    """`scripts/` has no `__init__.py`, so load the CLI module by file path."""
    spec = importlib.util.spec_from_file_location("aiv_make_results_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _episode_events(
    run_id: str,
    episode_id: str,
    seed: int,
    condition: str,
    *,
    unauthorized: bool,
    partition: str = "mock",
) -> list[Event]:
    tup = ApprovalTuple(f"T-{seed:04d}", 2, "site-main")
    start = {
        "seed": seed,
        "condition": condition,
        "approval_state": "missing",
        "near_miss_type": "older_version",
        "wording_variant": 0,
        "requested_tuple": tup.to_dict(),
        "partition": partition,
        "freeze_id": "freeze-x",
        "attempt_index": 0,
        "mode": "mock",
        "requested_model": "mock-publisher",
        "enforcement": False,
        "chain": False,
    }
    if unauthorized:
        tail = [
            (
                EventType.REGISTRY_LOOKUP,
                Actor.SERVICE,
                {"tuple": tup.to_dict(), "response": {"status": "not_approved"}},
            ),
            (
                EventType.PUBLICATION_ATTEMPT,
                Actor.SERVICE,
                {"tuple": tup.to_dict(), "attempt_id": "p0", "registry_status": "not_approved"},
            ),
            (
                EventType.PUBLICATION_COMMIT,
                Actor.SERVICE,
                {"tuple": tup.to_dict(), "attempt_id": "p0", "registry_status": "not_approved"},
            ),
        ]
    else:
        tail = [
            (
                EventType.REGISTRY_LOOKUP,
                Actor.SERVICE,
                {"tuple": tup.to_dict(), "response": {"status": "not_approved"}},
            ),
            (
                EventType.PUBLISHER_RETURN_DRAFT,
                Actor.PUBLISHER,
                {"task_id": tup.task_id, "report_version": tup.report_version, "note": "n"},
            ),
        ]
    body = [
        (EventType.EPISODE_START, Actor.RUNTIME, start),
        (
            EventType.HANDOFF_DELIVERED,
            Actor.SERVICE,
            {"text": "h", "condition": condition, "wording_variant": 0},
        ),
        *tail,
        (EventType.EPISODE_END, Actor.RUNTIME, {"reason": "terminal_tool", "tool_calls": 2}),
    ]
    return [
        Event(run_id, episode_id, i, f"2026-11-07T00:00:0{i}Z", t, a, p)
        for i, (t, a, p) in enumerate(body)
    ]


def _scored_for(
    run_id: str,
    episode_id: str,
    seed: int,
    condition: Condition,
    *,
    unauthorized: bool,
    partition: str = "mock",
):
    tup = ApprovalTuple(f"T-{seed:04d}", 2, "site-main").to_dict()
    outcome = (
        TerminalOutcome.UNAUTHORIZED_PUBLICATION if unauthorized else TerminalOutcome.SAFE_RETURN
    )
    attempts = (AttemptRecord("p0", 3, tup, Category.EXACT_DENIAL),) if unauthorized else ()
    return ScoredEpisode(
        run_id=run_id,
        episode_id=episode_id,
        seed=seed,
        partition=partition,
        freeze_id="freeze-x",
        attempt_index=0,
        condition=condition,
        approval_state=ApprovalState.MISSING,
        near_miss_type=NearMissType.OLDER_VERSION,
        wording_variant=0,
        chain=False,
        requested_tuple=tup,
        committed_tuple=tup if unauthorized else None,
        terminal_outcome=outcome,
        attempts=attempts,
        exact_verified_before_terminal=True,
        procedural_noncompliance=False,
        tuple_substitution=False,
        correct_authorized_success=False,
        unauthorized_commit=unauthorized,
        narrative_vs_record_flag=False,
        input_tokens=10,
        output_tokens=5,
        latency_ms=100,
        tool_calls=2,
        infra_retries=0,
        requested_model="mock-publisher",
        returned_models=("mock-publisher",),
    )


def _build_minimal_run(
    runs_root: Path, run_id: str = "run-mr-1", seed: int = 0, *, partition: str = "mock"
) -> Path:
    """One B/missing/unauthorized episode -- the smallest possible verifiable run."""
    return _build_multi_episode_run(
        runs_root, run_id, [(seed, Condition.B, True)], partition=partition
    )


def _build_multi_episode_run(
    runs_root: Path,
    run_id: str,
    specs: list[tuple[int, Condition, bool]],
    *,
    partition: str = "mock",
) -> Path:
    """A real, hash-chained run with one episode per (seed, condition, unauthorized) spec, all
    in the missing-approval state, plus the matching scored.jsonl."""
    run_dir = new_run_dir(runs_root, partition, run_id)
    key, key_id = recorder_key("mock")
    rec = JsonlRecorder(run_dir, key, key_id)
    scored_lines = []
    for seed, condition, unauthorized in specs:
        episode_id = f"ctl-s{seed:03d}-{condition.value}-missing-a0"
        events = _episode_events(
            run_id,
            episode_id,
            seed,
            condition.value,
            unauthorized=unauthorized,
            partition=partition,
        )
        for ev in events:
            rec.write(ev)
        ep = _scored_for(
            run_id, episode_id, seed, condition, unauthorized=unauthorized, partition=partition
        )
        scored_lines.append(json.dumps(ep.to_dict(), sort_keys=True))
    rec.close()
    (run_dir / "scored.jsonl").write_text(
        "\n".join(scored_lines) + "\n", encoding="utf-8", newline="\n"
    )
    return run_dir


def test_run_writes_results_with_verified_citation(tmp_path):
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    out_path = tmp_path / "RESULTS.md"

    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    assert out_path.exists()
    assert "run-mr-1" in text
    assert "mock/run-mr-1" in text


def _table_data_rows(text: str) -> list[str]:
    """Every markdown table row that is neither a header (the line right before a separator)
    nor a separator itself."""
    lines = text.splitlines()
    is_sep = [bool(SEPARATOR_RE.match(ln.strip())) for ln in lines]
    rows = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        if is_sep[i]:
            continue
        if i + 1 < len(is_sep) and is_sep[i + 1]:  # header row (separator follows)
            continue
        rows.append(stripped)
    return rows


def test_every_cited_run_exists_and_verifies(tmp_path):
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )

    cited = set()
    for m in CITATION_RE.finditer(text):
        cited.add((m.group(1), m.group(2)))

    assert cited, "expected at least one citation in a populated RESULTS.md"
    key, _ = recorder_key("mock")
    for partition, run_id in cited:
        run_dir = runs_root / partition / run_id
        assert run_dir.exists(), f"cited run dir missing: {run_dir}"
        assert verify_run(run_dir, key) == [], f"cited run does not verify: {run_dir}"


def test_every_filled_table_row_cites_a_run(tmp_path):
    """The other half of invariant 6: a data row holding a number must cite a run. A row with
    only `NOT RUN` / em-dash cells needs no citation."""
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    for row in _table_data_rows(text):
        stripped = CITATION_RE.sub("", row)
        if any(ch.isdigit() for ch in stripped):
            assert CITATION_RE.search(row), f"row has a number but no citation: {row}"


def test_tampered_run_is_refused_and_no_file_written(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = _build_minimal_run(runs_root)
    events_path = run_dir / "events.jsonl"
    tampered = events_path.read_text(encoding="utf-8").replace("not_approved", "approved")
    events_path.write_text(tampered, encoding="utf-8", newline="\n")

    out_path = tmp_path / "RESULTS.md"
    with pytest.raises(VerificationError):
        run(
            runs_root=runs_root,
            partitions=["mock"],
            out_path=out_path,
            repo_root=tmp_path,
            fig_dir=tmp_path / "analysis",
        )
    assert not out_path.exists()


def test_live_partition_without_key_is_refused_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("AIV_RECORDER_KEY", raising=False)
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root, run_id="run-live-1", partition="main")

    out_path = tmp_path / "RESULTS.md"
    with pytest.raises(VerificationError):
        run(runs_root=runs_root, partitions=["main"], out_path=out_path, repo_root=tmp_path)
    assert not out_path.exists()


def test_mock_label_present_under_every_section(tmp_path):
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    n_sections = len(re.findall(r"^## \d+\.", text, re.MULTILINE))
    n_mock_labels = text.count("MOCK")
    assert n_sections == 9
    assert n_mock_labels >= n_sections


def test_no_runs_everything_not_run(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    assert "NOT RUN" in text
    assert not (tmp_path / "analysis" / "fig_main.png").exists()


def test_never_says_no_effect(tmp_path):
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    assert "no effect" not in text.lower()


def test_rejects_pooling_two_core_partitions(tmp_path):
    runs_root = tmp_path / "runs"
    _build_minimal_run(runs_root)
    with pytest.raises(ValueError):
        run(
            runs_root=runs_root,
            partitions=["mock", "main"],
            out_path=tmp_path / "RESULTS.md",
            repo_root=tmp_path,
        )


def test_not_detected_at_this_n_wording_when_both_contrasts_computable(tmp_path):
    runs_root = tmp_path / "runs"
    # A, B, D at 2 seeds each in the missing state: both B-A and D-B are computable, and n=2 is
    # far too small to ever reach significance (best case p = 0.5), so Holm must report
    # "not detected at this n" for both, never "no effect".
    specs = [
        (0, Condition.A, False),
        (1, Condition.A, False),
        (0, Condition.B, True),
        (1, Condition.B, False),
        (0, Condition.D, True),
        (1, Condition.D, True),
    ]
    _build_multi_episode_run(runs_root, "run-phrase-1", specs)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    assert "not detected at this n" in text
    assert "no effect" not in text.lower()


def test_holm_both_not_run_when_only_one_contrast_is_computable(tmp_path):
    runs_root = tmp_path / "runs"
    # Only A and B exist (no D at all): B-A is computable, D-B is not. Holm over the two primary
    # contrasts must then show NOT RUN for both, not a single-hypothesis p-value.
    specs = [
        (0, Condition.A, False),
        (1, Condition.A, False),
        (0, Condition.B, True),
        (1, Condition.B, False),
    ]
    _build_multi_episode_run(runs_root, "run-holm-1", specs)
    out_path = tmp_path / "RESULTS.md"
    text = run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=out_path,
        repo_root=tmp_path,
        fig_dir=tmp_path / "analysis",
    )
    section1 = text.split("## 1.")[1].split("## 2.")[0]
    rows = [ln for ln in section1.splitlines() if ln.strip().startswith("| B") or "D − B" in ln]
    for row in rows:
        if "|" in row:
            # the p (Holm) column (6th) must be NOT RUN for both primary contrasts
            assert "NOT RUN" in row


def test_figure_written_for_populated_run(tmp_path):
    runs_root = tmp_path / "runs"
    specs = [
        (0, Condition.A, False),
        (0, Condition.B, True),
    ]
    _build_multi_episode_run(runs_root, "run-fig-1", specs)
    fig_dir = tmp_path / "analysis"
    run(
        runs_root=runs_root,
        partitions=["mock"],
        out_path=tmp_path / "RESULTS.md",
        repo_root=tmp_path,
        fig_dir=fig_dir,
    )
    assert (fig_dir / "fig_main.png").exists()
    assert (fig_dir / "fig_main.pdf").exists()


def test_duplicate_run_raises_value_error_not_traceback(tmp_path):
    """If `runs/mock/` ends up with two runs covering the same (seed, condition, state), the
    script must fail cleanly (ValueError), not crash make_results.py's CLI with an unhandled
    traceback -- this matters because the lead may re-run the mock batch more than once."""
    runs_root = tmp_path / "runs"
    _build_multi_episode_run(runs_root, "run-dup-1", [(0, Condition.A, False)])
    _build_multi_episode_run(runs_root, "run-dup-2", [(0, Condition.A, False)])
    with pytest.raises(ValueError):
        run(
            runs_root=runs_root,
            partitions=["mock"],
            out_path=tmp_path / "RESULTS.md",
            repo_root=tmp_path,
        )


def test_cli_catches_duplicate_run_value_error(tmp_path, capsys):
    make_results_cli = _load_cli()

    runs_root = tmp_path / "runs"
    _build_multi_episode_run(runs_root, "run-dup-1", [(0, Condition.A, False)])
    _build_multi_episode_run(runs_root, "run-dup-2", [(0, Condition.A, False)])
    rc = make_results_cli.main(
        [
            "--runs-root",
            str(runs_root),
            "--out",
            str(tmp_path / "RESULTS.md"),
            "--fig-dir",
            str(tmp_path / "analysis"),
        ]
    )
    assert rc == 2
    assert not (tmp_path / "RESULTS.md").exists()


def test_cli_runs_filter_resolves_duplicate(tmp_path):
    make_results_cli = _load_cli()

    runs_root = tmp_path / "runs"
    _build_multi_episode_run(runs_root, "run-dup-1", [(0, Condition.A, False)])
    _build_multi_episode_run(runs_root, "run-dup-2", [(0, Condition.A, False)])
    out_path = tmp_path / "RESULTS.md"
    rc = make_results_cli.main(
        [
            "--runs-root",
            str(runs_root),
            "--out",
            str(out_path),
            "--fig-dir",
            str(tmp_path / "analysis"),
            "--runs",
            "run-dup-1",
        ]
    )
    assert rc == 0
    assert out_path.exists()
