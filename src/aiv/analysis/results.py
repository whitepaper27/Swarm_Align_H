"""Build `RESULTS.md` from `runs/<partition>/<run_id>/scored.jsonl` (ARCHITECTURE §7a, §9).

Entry point for `scripts/make_results.py`. Verifies every cited run's hash chain first (invariant
6) and refuses to cite — and refuses to write any file at all — if a cited run fails
verification. Analysis reads only scoring output; the only thing read from `events.jsonl` is the
hash chain itself, via `aiv.recorder.verify_run`.

Partitions are never pooled (AGENTS.md): `mock`/`main` feed sections 1-5, `live` feeds section 6,
`confirm` feeds section 7, `pilot`/`discovery` feed section 9 only, never sections 1-5.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from aiv.analysis import figures, tables
from aiv.analysis.stats import holm_adjust
from aiv.analysis.tables import CellKey, Citation
from aiv.contract import ApprovalState, Condition, Mode, ScoredEpisode
from aiv.recorder import recorder_key, verify_run

CORE_PARTITIONS = ("mock", "main")
LIVE_PARTITIONS = ("live",)
CONFIRM_PARTITIONS = ("confirm",)
EXPLORATORY_PARTITIONS = ("pilot", "discovery")
ALL_KNOWN_PARTITIONS = (
    CORE_PARTITIONS + LIVE_PARTITIONS + CONFIRM_PARTITIONS + EXPLORATORY_PARTITIONS
)

NOT_RUN = "NOT RUN"


class VerificationError(RuntimeError):
    """A cited run's hash chain does not verify, or its key cannot be obtained. Writing a
    RESULTS.md is refused when this is raised."""


# ---------------------------------------------------------------------------------------------
# Verification gate (invariant 6)
# ---------------------------------------------------------------------------------------------


def verify_citations(runs_root: Path, citations: Iterable[Citation]) -> None:
    faults: list[str] = []
    for c in sorted(set(citations), key=lambda c: (c.partition, c.run_id)):
        mode = Mode.MOCK if c.partition == "mock" else Mode.LIVE
        try:
            key, _key_id = recorder_key(mode)
        except RuntimeError as exc:
            faults.append(f"{c}: {exc}")
            continue
        run_dir = runs_root / c.partition / c.run_id
        if not run_dir.exists():
            faults.append(f"{c}: run dir missing: {run_dir}")
            continue
        vf = verify_run(run_dir, key)
        if vf:
            faults.append(f"{c}: " + "; ".join(vf))
    if faults:
        raise VerificationError("refused to cite unverified run(s):\n" + "\n".join(faults))


# ---------------------------------------------------------------------------------------------
# Misc header helpers
# ---------------------------------------------------------------------------------------------


def git_commit(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _fmt_models(episodes: Sequence[ScoredEpisode]) -> str:
    requested = sorted({e.requested_model for e in episodes if e.requested_model})
    returned = sorted({m for e in episodes for m in e.returned_models})
    if not requested and not returned:
        return NOT_RUN
    return f"{', '.join(requested) or NOT_RUN} → {', '.join(returned) or NOT_RUN}"


def _fmt_freeze(episodes: Sequence[ScoredEpisode]) -> str:
    freeze_ids = sorted({e.freeze_id for e in episodes if e.freeze_id})
    return ", ".join(freeze_ids) if freeze_ids else NOT_RUN


def _pct(x: float | None) -> str:
    return NOT_RUN if x is None else f"{x:+.3f}"


def _ci(ci: tuple[float, float] | None) -> str:
    return NOT_RUN if ci is None else f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


def _frac(k: int, n: int) -> str:
    return f"{k}/{n}"


def _cp(k: int, n: int) -> str:
    from aiv.analysis.stats import clopper_pearson

    if n == 0:
        return NOT_RUN
    lo, hi = clopper_pearson(k, n)
    return f"[{lo:.3f}, {hi:.3f}]"


def _rate_cell(k: int, n: int) -> str:
    """k/n with an exact CP CI. A zero cell reads '0/n observed, upper bound U' (never 'safe')."""
    from aiv.analysis.stats import clopper_pearson

    if n == 0:
        return NOT_RUN
    if k == 0:
        _, hi = clopper_pearson(k, n)
        return f"0/{n} observed, upper bound {hi:.3f}"
    lo, hi = clopper_pearson(k, n)
    return f"{k}/{n}, CP [{lo:.3f}, {hi:.3f}]"


def _cite_list(cites: Iterable[Citation]) -> str:
    cs = sorted(set(cites), key=lambda c: (c.partition, c.run_id))
    return ", ".join(f"`{c}`" for c in cs) if cs else NOT_RUN


# ---------------------------------------------------------------------------------------------
# Core (sections 1-5)
# ---------------------------------------------------------------------------------------------


class CoreData:
    def __init__(
        self,
        partition: str | None,
        finals: dict[CellKey, ScoredEpisode],
        retries: dict[CellKey, int] | None = None,
    ):
        self.partition = partition
        self.finals = finals
        self.retries = retries or {}
        self.cells: dict[tuple[Condition, ApprovalState], list[ScoredEpisode]] = {}
        if partition is not None:
            for cond in tables.CONDITIONS:
                for state in tables.STATES:
                    self.cells[(cond, state)] = tables.cell(finals, partition, cond, state)

    @property
    def episodes(self) -> list[ScoredEpisode]:
        out: list[ScoredEpisode] = []
        for grp in self.cells.values():
            out.extend(grp)
        return out

    def get(self, cond: Condition, state: ApprovalState) -> list[ScoredEpisode]:
        return self.cells.get((cond, state), [])

    def retries_for(self, cond: Condition, state: ApprovalState) -> int:
        return sum(
            v for k, v in self.retries.items() if k.condition == cond and k.approval_state == state
        )


def build_core(all_episodes: Sequence[ScoredEpisode], core_partition: str | None) -> CoreData:
    if core_partition is None:
        return CoreData(None, {})
    non_chain = [e for e in all_episodes if e.partition == core_partition and not e.chain]
    finals_all = tables.select_final_attempts(non_chain)
    finals = {k: v for k, v in finals_all.items() if k.partition == core_partition}
    tables.assert_single_freeze(finals.values(), where="core tables (sections 1-5)")
    retries = {
        k: v for k, v in tables.retry_counts(non_chain).items() if k.partition == core_partition
    }
    return CoreData(core_partition, finals, retries)


def section1(core: CoreData) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    rows = []
    results = {}
    for name, (a_key, b_key) in (
        ("B − A (assertion)", ((Condition.A,), (Condition.B,))),
        ("D − B (provenance under assertion)", ((Condition.B,), (Condition.D,))),
    ):
        a_cond, b_cond = a_key[0], b_key[0]
        a_grp = core.get(a_cond, ApprovalState.MISSING)
        b_grp = core.get(b_cond, ApprovalState.MISSING)
        pc = tables.paired_flag(a_grp, b_grp, flag="unauthorized_commit")
        results[name] = pc
        cites |= set(pc.citations)

    # Holm over exactly the two primary contrasts; if either is uncomputable, both stay NOT RUN.
    computable = all(r.n > 0 for r in results.values())
    if computable:
        raw_ps = [_p_from_pair(r) for r in results.values()]
        holm_ps = holm_adjust(raw_ps)
    else:
        holm_ps = [None, None]

    for (name, r), p_adj in zip(results.items(), holm_ps, strict=True):
        p_raw_val = _p_from_pair(r) if r.n > 0 else None
        disc = f"+{r.pos}/−{r.neg}" if r.n else NOT_RUN
        n_cell = r.n if r.n else NOT_RUN
        diff = _pct(r.diff)
        p_cell = f"{p_raw_val:.5g}" if p_raw_val is not None else NOT_RUN
        padj_cell = f"{p_adj:.5g}" if p_adj is not None else NOT_RUN
        runs_cell = _cite_list(r.citations) if r.n else NOT_RUN
        rows.append(
            f"| {name} | {disc} | {n_cell} | {diff} | {p_cell} | {padj_cell} | {runs_cell} |"
        )
    header = (
        "| Contrast | Discordant (+/−) | Paired n | Difference | p (exact) | p (Holm) | Runs |\n"
        "|---|---|---|---|---|---|---|"
    )
    interp = [
        _interpret_p(name, p_adj) for name, p_adj in zip(results.keys(), holm_ps, strict=True)
    ]
    body = header + "\n" + "\n".join(rows) + "\n\n" + "\n".join(interp)
    return body, cites


def _interpret_p(name: str, p_adj: float | None) -> str:
    if p_adj is None:
        return f"- {name}: {NOT_RUN}."
    if p_adj < 0.05:
        return f"- {name}: detected at this n (p (Holm) = {p_adj:.5g})."
    return f"- {name}: not detected at this n (p (Holm) = {p_adj:.5g})."


def _interpret_ci(name: str, ci: tuple[float, float] | None) -> str:
    if ci is None:
        return f"- {name}: {NOT_RUN}."
    lo, hi = ci
    if lo <= 0.0 <= hi:
        return f"- {name}: not detected at this n (95% bootstrap CI crosses 0)."
    return f"- {name}: detected at this n (95% bootstrap CI excludes 0)."


def _p_from_pair(pc) -> float:
    from aiv.analysis.stats import mcnemar_exact_p

    return mcnemar_exact_p(pc.pos, pc.neg)


def section2(
    core: CoreData, *, n_boot: int = 10000, rng_seed: int = 0
) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    a = core.get(Condition.A, ApprovalState.MISSING)
    b = core.get(Condition.B, ApprovalState.MISSING)
    c = core.get(Condition.C, ApprovalState.MISSING)
    d = core.get(Condition.D, ApprovalState.MISSING)

    point_ca, ci_ca, n_ca, cites_ca = tables.bootstrap_simple_diff(
        a, c, n_boot=n_boot, rng_seed=rng_seed
    )
    cites |= set(cites_ca)
    point_int, ci_int, n_int, cites_int = tables.bootstrap_interaction(
        a, b, c, d, n_boot=n_boot, rng_seed=rng_seed
    )
    cites |= set(cites_int)

    ca_est, ca_ci, ca_runs = (
        _pct(point_ca),
        _ci(ci_ca) if n_ca else NOT_RUN,
        (_cite_list(cites_ca) if n_ca else NOT_RUN),
    )
    int_est, int_ci, int_runs = (
        _pct(point_int),
        _ci(ci_int) if n_int else NOT_RUN,
        (_cite_list(cites_int) if n_int else NOT_RUN),
    )
    rows = [
        f"| C − A | {ca_est} | {ca_ci} | {ca_runs} |",
        f"| (D − C) − (B − A) interaction | {int_est} | {int_ci} | {int_runs} |",
    ]
    header = "| Contrast | Estimate | 95% seed-bootstrap CI | Runs |\n|---|---|---|---|"
    interp = [
        _interpret_ci("C − A", ci_ca if n_ca else None),
        _interpret_ci("(D − C) − (B − A) interaction", ci_int if n_int else None),
    ]
    body = header + "\n" + "\n".join(rows) + "\n\n" + "\n".join(interp)
    return body, cites


def section3(core: CoreData) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    rows = []
    for cond in tables.CONDITIONS:
        for state in tables.STATES:
            grp = core.get(cond, state)
            run_cites = {citation_of(e) for e in grp}
            cites |= run_cites
            run_cell = _cite_list(run_cites) if grp else NOT_RUN

            if state == ApprovalState.MISSING:
                k, n = tables.unauthorized_commit_rate(grp)
                unauth = _rate_cell(k, n)
                correct = "—"
                k_s, n_s = tables.safe_outcome_rate(grp)
                safe = _frac(k_s, n_s) if n_s else NOT_RUN
            else:
                unauth = "—"
                k_c, n_c = tables.correct_authorized_rate(grp)
                correct = _rate_cell(k_c, n_c)
                safe = "—"

            k_v, n_v = tables.exact_verification_rate(grp)
            verif = _rate_cell(k_v, n_v)

            row = (
                f"| {cond.value} | {state.value} | {unauth} | {verif} | "
                f"{correct} | {safe} | {run_cell} |"
            )
            rows.append(row)
    header = (
        "| Cond | State | Unauthorized commits / missing eps | "
        "Exact verification before decision / evaluable | "
        "Correct authorized success / approved eps | "
        "Safe return + approval request / missing eps | Run file |\n"
        "|---|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows), cites


def section3b(core: CoreData) -> tuple[str, set[Citation]]:
    """Flags (tuple substitution, procedural noncompliance, narrative-vs-record) and
    tokens/latency/tool calls per cell — ARCHITECTURE §7a lists these as main metrics too."""
    cites: set[Citation] = set()
    rows = []
    for cond in tables.CONDITIONS:
        for state in tables.STATES:
            grp = core.get(cond, state)
            run_cites = {citation_of(e) for e in grp}
            cites |= run_cites
            if not grp:
                cells = " | ".join([NOT_RUN] * 7)
                rows.append(f"| {cond.value} | {state.value} | {cells} |")
                continue
            n = len(grp)
            flags = tables.flag_counts(grp)
            means = tables.token_latency_means(grp)
            runs = _cite_list(run_cites)
            row = (
                f"| {cond.value} | {state.value} | "
                f"{flags['tuple_substitution']}/{n} | {flags['procedural_noncompliance']}/{n} | "
                f"{flags['narrative_vs_record_flag']}/{n} | "
                f"{means['input_tokens']:.0f} | {means['output_tokens']:.0f} | "
                f"{means['latency_ms']:.0f} | {means['tool_calls']:.1f} | {runs} |"
            )
            rows.append(row)
    header = (
        "| Cond | State | Tuple substitution | Procedural noncompliance | "
        "Narrative-vs-record | Mean input tok | Mean output tok | Mean latency (ms) | "
        "Mean tool calls | Runs |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows), cites


def citation_of(e: ScoredEpisode) -> Citation:
    return Citation(e.partition, e.run_id)


def section4(core: CoreData) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    rows = []
    for cond in tables.CONDITIONS:
        for state in tables.STATES:
            grp = core.get(cond, state)
            run_cites = {citation_of(e) for e in grp}
            cites |= run_cites
            counts = tables.category_counts(grp)
            if not grp:
                cells = " | ".join([NOT_RUN] * 5)
                rows.append(f"| {cond.value} | {state.value} | {cells} |")
                continue
            nl = counts.get("no_lookup", 0)
            nm = counts.get("nonmatching_lookup_only", 0)
            ed = counts.get("exact_denial", 0)
            ea = counts.get("exact_approval", 0)
            runs = _cite_list(run_cites)
            rows.append(f"| {cond.value} | {state.value} | {nl} | {nm} | {ed} | {ea} | {runs} |")
    header = (
        "| Cond | State | no_lookup | nonmatching_lookup_only | exact_denial | "
        "exact_approval | Runs |\n"
        "|---|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows), cites


def section5(core: CoreData) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    rows = []
    for cond in tables.CONDITIONS:
        for state in tables.STATES:
            grp = core.get(cond, state)
            run_cites = {citation_of(e) for e in grp}
            cites |= run_cites
            if not grp:
                cells = " | ".join([NOT_RUN] * 8)
                rows.append(f"| {cond.value} | {state.value} | {cells} |")
                continue
            tc = tables.terminal_outcome_counts(grp)
            evaluable = tables.evaluable_count(grp)
            sr = tc.get("safe_return", 0)
            ar = tc.get("approval_request", 0)
            rf = tc.get("refusal", 0)
            be = tc.get("budget_exhausted", 0)
            ia = tc.get("invalid_action", 0)
            ie = tc.get("infra_error", 0)
            retries = core.retries_for(cond, state)
            runs = _cite_list(run_cites)
            row = (
                f"| {cond.value} | {state.value} | {sr} | {ar} | {rf} | {be} | {ia} | {ie} | "
                f"{retries} | {evaluable}/{len(grp)} ({runs}) |"
            )
            rows.append(row)
    header = (
        "| Cond | State | safe_return | approval_request | refusal | budget_exhausted | "
        "invalid_action | infra_error | Infra retries | Evaluable / scheduled (Runs) |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows), cites


# ---------------------------------------------------------------------------------------------
# Non-core sections (6, 7, 8, 9) — best-effort; most sources are unavailable to analysis and
# stay NOT RUN (manual labels, spend in USD: not part of `scored.jsonl`).
# ---------------------------------------------------------------------------------------------


def section6(live_episodes: Sequence[ScoredEpisode]) -> tuple[str, set[Citation]]:
    chain_eps = [e for e in live_episodes if e.chain]
    header = (
        "| Variant | Episodes | Unauthorized commits | False approval claims "
        "(worker-orig / coord-introduced / coord-strengthened / ambiguous) | Runs |\n"
        "|---|---|---|---|---|"
    )
    if not chain_eps:
        rows = [
            f"| Ordinary | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} |",
            f"| Adversarial worker | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} |",
        ]
        return header + "\n" + "\n".join(rows), set()
    cites = {citation_of(e) for e in chain_eps}
    unauth = sum(1 for e in chain_eps if e.unauthorized_commit)
    runs = _cite_list(cites)
    row = (
        f"| All (ordinary/adversarial split requires manual labels) | {len(chain_eps)} | "
        f"{unauth} | {NOT_RUN} | {runs} |"
    )
    return header + "\n" + row, cites


def section7(confirm_episodes: Sequence[ScoredEpisode]) -> tuple[str, set[Citation]]:
    if not confirm_episodes:
        return NOT_RUN, set()
    cites = {citation_of(e) for e in confirm_episodes}
    unauth = sum(1 for e in confirm_episodes if e.unauthorized_commit)
    text = (
        f"Confirmation experiment episodes: {len(confirm_episodes)}; unauthorized commits: "
        f"{unauth}. Runs: {_cite_list(cites)}."
    )
    return text, cites


def section8(by_partition: dict[str, Sequence[ScoredEpisode]]) -> tuple[str, set[Citation]]:
    cites: set[Citation] = set()
    rows = []
    for label, parts in (
        ("pilot", ["pilot"]),
        ("discovery", ["discovery"]),
        ("main", ["main"]),
        ("live", ["live"]),
        ("confirm", ["confirm"]),
    ):
        eps: list[ScoredEpisode] = []
        for p in parts:
            eps.extend(by_partition.get(p, []))
        if not eps:
            rows.append(
                f"| {label} | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | see GOAL.md |"
            )
            continue
        run_cites = {citation_of(e) for e in eps}
        cites |= run_cites
        in_tok = sum(e.input_tokens for e in eps)
        out_tok = sum(e.output_tokens for e in eps)
        runs = _cite_list(run_cites)
        row = (
            f"| {label} | {NOT_RUN} | {in_tok} | {out_tok} | "
            f"{NOT_RUN} (USD not in scored.jsonl; Runs: {runs}) | see GOAL.md |"
        )
        rows.append(row)
    header = (
        "| Partition | Calls | Input tok | Output tok | Spend (USD) | Cap |\n"
        "|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows), cites


def section9(exploratory_episodes: Sequence[ScoredEpisode]) -> tuple[str, set[Citation]]:
    if not exploratory_episodes:
        return NOT_RUN, set()
    cites = {citation_of(e) for e in exploratory_episodes}
    by_partition: dict[str, list[ScoredEpisode]] = {}
    for e in exploratory_episodes:
        by_partition.setdefault(e.partition, []).append(e)
    lines = ["Exploratory only. Never pooled into the confirmatory contrasts above (sections 1-2)."]
    for part, eps in sorted(by_partition.items()):
        unauth = sum(1 for e in eps if e.unauthorized_commit)
        lines.append(f"- `{part}`: {len(eps)} episodes, {unauth} unauthorized commits.")
    lines.append(f"Runs: {_cite_list(cites)}.")
    return "\n".join(lines), cites


# ---------------------------------------------------------------------------------------------
# Top-level render
# ---------------------------------------------------------------------------------------------


def render(
    runs_root: Path,
    partitions: Sequence[str],
    *,
    repo_root: Path | None = None,
    n_boot: int = 10000,
    rng_seed: int = 0,
    run_ids: Sequence[str] | None = None,
) -> tuple[str, bool]:
    """Returns (markdown_text, mock_included). Raises VerificationError before writing anything
    if any run that would be cited fails hash-chain verification. `run_ids`, if given, restricts
    every partition's episodes to those run IDs (lets the lead pick one run out of several,
    e.g. after re-running the mock batch and leaving old run dirs in place)."""
    core_requested = [p for p in partitions if p in CORE_PARTITIONS]
    if len(core_requested) > 1:
        raise ValueError(
            f"never pool core partitions into one table; got {core_requested} (pass only one of "
            f"{CORE_PARTITIONS})"
        )
    core_partition = core_requested[0] if core_requested else None
    mock_included = "mock" in partitions

    run_id_set = set(run_ids) if run_ids else None
    by_partition: dict[str, list[ScoredEpisode]] = {}
    for p in partitions:
        eps = tables.load_partition_episodes(runs_root, p)
        if run_id_set is not None:
            eps = [e for e in eps if e.run_id in run_id_set]
        by_partition[p] = eps
    all_eps = [e for eps in by_partition.values() for e in eps]

    core = build_core(all_eps, core_partition)

    live_eps: list[ScoredEpisode] = []
    for p in LIVE_PARTITIONS:
        live_eps.extend(by_partition.get(p, []))
    live_finals = (
        {k: v for k, v in tables.select_final_attempts(live_eps).items()} if live_eps else {}
    )
    live_finals_list = list(live_finals.values())

    confirm_eps: list[ScoredEpisode] = []
    for p in CONFIRM_PARTITIONS:
        confirm_eps.extend(by_partition.get(p, []))
    confirm_finals_list = (
        list(tables.select_final_attempts(confirm_eps).values()) if confirm_eps else []
    )

    exploratory_eps: list[ScoredEpisode] = []
    for p in EXPLORATORY_PARTITIONS:
        exploratory_eps.extend(by_partition.get(p, []))
    exploratory_finals_list = (
        list(tables.select_final_attempts(exploratory_eps).values()) if exploratory_eps else []
    )

    s1, c1 = section1(core)
    s2, c2 = section2(core, n_boot=n_boot, rng_seed=rng_seed)
    s3, c3 = section3(core)
    s3b, c3b = section3b(core)
    s4, c4 = section4(core)
    s5, c5 = section5(core)
    s6, c6 = section6(live_finals_list)
    s7, c7 = section7(confirm_finals_list)
    s8, c8 = section8(
        {
            "pilot": [e for e in exploratory_finals_list if e.partition == "pilot"],
            "discovery": [e for e in exploratory_finals_list if e.partition == "discovery"],
            "main": core.episodes if core.partition == "main" else [],
            "live": live_finals_list,
            "confirm": confirm_finals_list,
        }
    )
    s9, c9 = section9(exploratory_finals_list)

    all_citations = c1 | c2 | c3 | c3b | c4 | c5 | c6 | c7 | c8 | c9
    verify_citations(runs_root, all_citations)

    header_episodes = core.episodes
    freeze_id = _fmt_freeze(header_episodes)
    models = _fmt_models(header_episodes)
    commit = git_commit(repo_root) if repo_root else "unknown"

    mock_note = ""
    if mock_included:
        mock_note = "\n> **MOCK — not a result.**"

    def mock_tag() -> str:
        return "\n\n*MOCK — not a result.*" if mock_included else ""

    text = f"""# RESULTS

> **GENERATED FILE — do not hand-edit.** Written by `scripts/make_results.py` from `runs/`.
> Every filled row cites its run ID and episode file. Rows read **`NOT RUN`** until real runs exist.
> Mock output, if rendered, is labeled **MOCK** and is not a result.{mock_note}

Freeze ID: `{freeze_id}` · Git commit: `{commit}` · Models (requested → returned): `{models}`

## 1. Primary contrasts (missing-approval state, unauthorized committed publication)

Exact McNemar on seed-paired binaries, Holm across the two rows.{mock_tag()}

{s1}

## 2. Secondary / exploratory contrasts{mock_tag()}

{s2}

## 3. Per-cell metrics (raw numerator/denominator, exact 95% Clopper–Pearson CI){mock_tag()}

{s3}

A zero cell is reported as "0/n observed, upper bound U" — never as "safe".

Flags and tokens/latency/tool calls (same cells):

{s3b}

## 4. Verification category per publication attempt{mock_tag()}

{s4}

## 5. Terminal outcomes, coverage and infrastructure failures{mock_tag()}

{s5}

## 6. Live three-role validation (separate; not pooled){mock_tag()}

{s6}

## 7. Confirmation experiment (only if run; frozen separately){mock_tag()}

{s7}

## 8. Cost{mock_tag()}

{s8}

## 9. Exploratory: pilot and discovery (never pooled with the above){mock_tag()}

{s9}
"""
    return text, mock_included


def make_figure(core: CoreData, out_dir: Path, *, mock: bool) -> Path | None:
    cell_rates: dict[str, dict[str, tuple[int, int]]] = {}
    for cond in tables.CONDITIONS:
        cell_rates[cond.value] = {
            "missing": tables.unauthorized_commit_rate(core.get(cond, ApprovalState.MISSING)),
            "approved": tables.correct_authorized_rate(core.get(cond, ApprovalState.APPROVED)),
        }
    if not figures.has_any_data(cell_rates):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "fig_main.png"
    pdf = out_dir / "fig_main.pdf"
    figures.plot_main_figure(cell_rates, str(png), str(pdf), mock=mock)
    return png


def run(
    *,
    runs_root: str | Path = "runs",
    partitions: Sequence[str] = ("mock",),
    out_path: str | Path = "RESULTS.md",
    repo_root: str | Path | None = None,
    fig_dir: str | Path = "analysis",
    n_boot: int = 10000,
    rng_seed: int = 0,
    run_ids: Sequence[str] | None = None,
) -> str:
    runs_root = Path(runs_root)
    repo_root = Path(repo_root) if repo_root else Path(".")
    text, mock_included = render(
        runs_root,
        list(partitions),
        repo_root=repo_root,
        n_boot=n_boot,
        rng_seed=rng_seed,
        run_ids=run_ids,
    )

    core_requested = [p for p in partitions if p in CORE_PARTITIONS]
    core_partition = core_requested[0] if core_requested else None
    if core_partition:
        all_eps = tables.load_partition_episodes(runs_root, core_partition)
        if run_ids:
            run_id_set = set(run_ids)
            all_eps = [e for e in all_eps if e.run_id in run_id_set]
        core = build_core(all_eps, core_partition)
        make_figure(core, Path(fig_dir), mock=mock_included)

    Path(out_path).write_text(text, encoding="utf-8", newline="\n")
    return text
