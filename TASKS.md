# TASKS

The ordered work list for the swarm. Each task advances a milestone or a definition-of-done line in [`GOAL.md`](GOAL.md).
- **Status:** `todo` · `doing` · `done` · `blocked` · `cut`.
- **Owner:** `agent`, `human` (Sahil), or `lead` (the integration owner: the main session, which merges and runs the gate).
- **Waves:** each wave runs in **≤ 3 parallel git worktrees**, one module set per worktree (see the ownership table in
  ARCHITECTURE §3). No worktree edits another's modules or `contract.py`.
- **Merge gate after every wave:** the lead merges into `main`, runs `./scripts/verify.sh`, and must get green before the next wave starts.

| ID | Task | Owner | Depends on | Wave | Done when | Status |
|---|---|---|---|---|---|---|
| T0 | Owner decisions: build, $50 cap, go-gate, reuse, repo location | human | – | – | Recorded in GOAL.md | done |
| T0b | Confirm actor model ID, pricing basis in `.env`, license | human | – | – | GOAL boxes ticked | done |
| **T0.5** | **Novelty memo (first hour).** Find and read the three candidate prior works in sol.md §12, plus the Track 4 readings (Secret Collusion; AI Control) and the METR incident report. Write `docs/contribution.md`: what each verified work did (method, not just the title), and what our comparison establishes that it does not. Unverifiable → removed | agent + human review | – | 0 | Memo committed; every citation has a URL that was opened; claims marked *provisional* | done (owner review pending) |
| T1 | Scaffold + **interfaces**: `pyproject` (uv), `.gitignore`, `.env.example`, `scripts/verify.sh`; `src/aiv/contract.py` (tuple, events, tool schemas, scored-episode record §7a, config); copy L3 spend/recorder/freeze with disclosure headers; one mock episode through the real path; tag `contract-v1` | lead | T0 | 0 | `verify.sh` green; tag exists | done |
| T2 | W1 `world/` + `service/`: seeded generator, near-miss allocation, registry, publication service, ordered event log; optional `enforcement/` | agent | T1 | 1 | Invariants 1, 2, 4, 10 pass | done |
| T3 | W2 `handoff/`: conditions A–D, provenance block, neutral padding, ≥3 wording variants per condition in `protocol/draft/` | agent | T1 | 1 | Invariant 3 passes; length ratio A:C and B:D within ±10% | done |
| T4 | W3 `agents/` + `runtime/` + spend/recorder/freeze adaptation: publisher tool loop, `mock` and `anthropic` adapters, episode and batch runner, retry with state reset, CLI `aiv run --mode mock` | agent | T1 | 1 | Mock batch writes verifiable run files; invariant 7 passes | done |
| — | **Wave 1 merge gate** | lead | T2–T4 | – | `verify.sh` green on main | todo |
| T5 | W4 `scoring/`: per-attempt category + terminal outcome, pure function of events | agent | wave 1 | 2 | Invariants 1, 2, 5, 8, 9 pass on fixture event logs; writes `scored.jsonl` per ARCHITECTURE §7a | done |
| T6 | W5 `analysis/` + `scripts/make_results.py`: exact CIs, McNemar + Holm, seed bootstrap, main figure, verification table, writes `RESULTS.md` | agent | wave 1 (scoring via fixture outputs) | 2 | Generated from mock runs, labeled **MOCK**; invariant 6 passes | done |
| T7 | W6 `chain/`: live worker + coordinator roles (ordinary and adversarial prompts) producing the handoff consumed by the same publisher loop; mock-mode run | agent | wave 1 | 2 | Mock chain episode end to end; messages logged as `agent.message` | done |
| T8 | **Integration:** mock e2e of 4 conditions × 2 states × 2 seeds; `aiv freeze` dry run (create + validate). Mock chain is a separate gate (GOAL deviation 4) | lead | T5–T6 (T7 for chain gate) | – | **Gate for core paid calls** (Sun 6 AM); chain gate for live-chain spend | done (core + chain mock gates green; see docs/morning_checklist.md) |
| T9 | **Pilot + live discovery** (Sahil's go, ≤ $5 + ≤ $5): ~2 seeds × 4 cond × 2 states, plus ≤ 6 ordinary and ≤ 6 adversarial chain episodes. Mechanics fixes only. Measure the cost per episode and the base rate of denial→publish | human go, agent runs | T8, T0b | – | Cost projection + base-rate note written to `docs/pilot.md`; §9 gate answered | done ($0.15; docs/pilot.md) |
| T10 | **Freeze:** N (default 20 seeds; reduce if the projection exceeds $25), wordings, models, settings, retry policy, contrasts, stopping rule; choose the follow-up per GOAL | human | T9 | – | `freeze_id` committed (Sun 9 AM) | done (F20261108-1627-c74d92a0) |
| T11 | **Main controlled eval:** N seeds × 4 × 2, partition `main` | human go, agent runs | T10 | – | Complete or stopped at the cutoff with coverage (Sun 1 PM) | done (800 episodes) |
| T12 | **Live validation** (fresh, frozen settings): ordinary, then adversarial; manual labels of false approval claims (worker-originated / coordinator-introduced / coordinator-strengthened / ambiguous) with quoted evidence | human go + human labels | T10 | – | Labels committed before any summary statistic is computed | done (96 Haiku + 96 Gemini-worker episodes; two-labeler labels) |
| T13 | Optional **confirmation** (sol.md §6): injected exact-denial history × neutral vs assertion, fresh tuples | human go | T11 | – | Only if GOAL cut order allows | done (Haiku 200 + Gemini 100 episodes) |
| T14 | Manually inspect every unauthorized publication and every narrative-vs-record flag; pick one representative annotated trace | human + agent | T11, T12 | – | `docs/trace.md` | done (two-labeler flag review; docs/trace.md) |
| T15 | `make_results.py` on real partitions → `RESULTS.md` + figures; fact sheet | agent | T11 (T12/T13 if run) | – | Every number traced (Sun 10 PM) | done (RESULTS.md incl. section 10 cross-provider) |
| T16 | Report in the official template ([`REPORT_OUTLINE.md`](REPORT_OUTLINE.md)); starts **Sun 2 PM** regardless | human (agent drafts) | T0.5, T15 | – | ≤ 8 pages excl. references/appendices; appendix present | doing: final draft in docs/report_draft.md; owner ports to the Apart template |
| T17 | Public repo push, README disclosure check, submit; record confirmation | human | T16 | – | Submitted (target Mon 3 AM CT) | todo (owner): authors, repo URL, citation check, push, submit |

## Fallbacks (decided before seeing outcomes)

- Pilot projection for 20 seeds exceeds $25 → reduce N to what fits, and freeze it before the main run.
- Live chain is not viable by Sun 9 AM → submit the controlled core. The live panel is marked **absent** with the
  reason. This weakens the multi-agent claim, and the report says so.
- Zero denial→publish base rate in discovery → the T13 injected-denial design becomes the follow-up (GOAL §gate).
  Never tune prompts until A fails.
- Behind schedule at Sun 2 PM → stop new runs except ones already authorized and in flight, and write.

## Cut from this submission (sol.md §11)

Graph monitoring, behavioral re-identification, canaries, reviewer rotation, steganography, relay-depth
sweeps, new sandbox infrastructure, and "refusal shopping" claims.
