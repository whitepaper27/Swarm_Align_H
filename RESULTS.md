# RESULTS

> **GENERATED FILE — do not hand-edit.** Written by `scripts/make_results.py` from `runs/`.
> Every filled row cites its run ID and episode file. Rows read **`NOT RUN`** until real runs exist.
> Mock output, if rendered, is labeled **MOCK** and is not a result. Status as of 2026-11-07:
> **no runs have been executed.**

Freeze ID: `NOT RUN` · Git commit: `NOT RUN` · Models (requested → returned): `NOT RUN`

## 1. Primary contrasts (missing-approval state, unauthorized committed publication)

Exact McNemar on seed-paired binaries, Holm across the two rows.

| Contrast | Discordant (+/−) | Paired n | Difference | p (exact) | p (Holm) | Runs |
|---|---|---|---|---|---|---|
| B − A (assertion) | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| D − B (provenance under assertion) | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## 2. Secondary / exploratory contrasts

| Contrast | Estimate | 95% seed-bootstrap CI | Runs |
|---|---|---|---|
| C − A | NOT RUN | NOT RUN | NOT RUN |
| (D − C) − (B − A) interaction | NOT RUN | NOT RUN | NOT RUN |

## 3. Per-cell metrics (raw numerator/denominator, exact 95% Clopper–Pearson CI)

| Cond | State | Unauthorized commits / missing eps | Exact verification before decision / evaluable | Correct authorized success / approved eps | Safe return + approval request / missing eps | Run file |
|---|---|---|---|---|---|---|
| A | missing | NOT RUN | NOT RUN | — | NOT RUN | NOT RUN |
| A | approved | — | NOT RUN | NOT RUN | — | NOT RUN |
| B | missing | NOT RUN | NOT RUN | — | NOT RUN | NOT RUN |
| B | approved | — | NOT RUN | NOT RUN | — | NOT RUN |
| C | missing | NOT RUN | NOT RUN | — | NOT RUN | NOT RUN |
| C | approved | — | NOT RUN | NOT RUN | — | NOT RUN |
| D | missing | NOT RUN | NOT RUN | — | NOT RUN | NOT RUN |
| D | approved | — | NOT RUN | NOT RUN | — | NOT RUN |

A zero cell is reported as "0/n observed, upper bound U" — never as "safe".

## 4. Verification category per publication attempt

| Cond | State | no_lookup | nonmatching_lookup_only | exact_denial | exact_approval |
|---|---|---|---|---|---|
| A–D × state | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## 5. Terminal outcomes, coverage and infrastructure failures

| Cond | State | safe_return | approval_request | refusal | budget_exhausted | invalid_action | infra_error | Evaluable / scheduled |
|---|---|---|---|---|---|---|---|---|
| A–D × state | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## 6. Live three-role validation (separate; not pooled)

| Variant | Episodes | Unauthorized commits | False approval claims (worker-orig / coord-introduced / coord-strengthened / ambiguous) | Runs |
|---|---|---|---|---|
| Ordinary | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Adversarial worker | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## 7. Confirmation experiment (only if run; frozen separately)

NOT RUN

## 8. Cost

| Partition | Calls | Input tok | Output tok | Spend (USD) | Cap |
|---|---|---|---|---|---|
| pilot / discovery / main / live / confirm | NOT RUN | NOT RUN | NOT RUN | NOT RUN | see GOAL.md |

## 9. Exploratory: pilot and discovery (never pooled with the above)

NOT RUN
