# GOAL

Every other decision defers to this file. The research plan is [`sol.md`](sol.md); this page
is its operating summary. **On science (conditions, metrics, claims), `sol.md` wins. On
schedule, scope and anything this page lists under "Deviations", this page wins.**
Design: [`ARCHITECTURE.md`](ARCHITECTURE.md). Work list: [`TASKS.md`](TASKS.md). Agent rules:
[`AGENTS.md`](AGENTS.md).

**Owner:** Sahil Soni (sole human reviewer, report author, spend authority) ·
**Last updated:** 2026-11-07

## The goal (one sentence)

By **Sun Nov 8 2026, 11:59 PM AoE (= Mon Nov 9, 5:59 AM US Central)**, with a target of
submitting by **Mon 3:00 AM Central**, submit a reproducible report to the Apart Swarm
Alignment Hackathon (Track 4 primary, Track 3 secondary) that measures **where approval
verification breaks in an agent handoff**. It distinguishes three cases: omitted checks, mismatched checks, and action after an
authoritative denial. It compares peer assertions with service-recorded provenance, and tests
whether the mechanism appears in a live three-agent workflow.

That sentence is a *design*, not a finding. The abstract states only what the runs measured.

## Framing guardrails

- This is an **authorization-verification** study. It is not a collusion study. A worker that makes false claims is a
  multi-agent *attack*. Calling it "collusion" requires evidence of knowing cooperation, which this design
  does not produce. Do not use the word for our results.
- Connect to the sprint theme (the July 2026 swarm incident) **only** with sources we have read. The METR
  report must be read before it is cited.
- Never claim "first" or "novel" without a verified comparison in `docs/contribution.md`.

## Owner decisions

- [x] **Build authorized** (2026-11-07). The project lives in its own repo at `D:\Eb1\swarm-alignment-2026`.
- [x] **Spend cap: $50 total**, enforced by the harness ledger (reserve-before-dispatch, see
      ARCHITECTURE §Spend). Sub-budgets: live discovery ≤ $5 · pilot ≤ $5 · main eval ≤ $25 ·
      live validation ≤ $8 · confirmation ≤ $5 · reserve $2.
- [x] **Paid calls only on Sahil's explicit go**, per batch. Agents build and test in `mock` mode
      and never start live calls on their own.
- [x] **Reuse:** copy selected modules from `Agent_World_L3` (same author, commit
      `61c7d371b3ca3bf082640a6a33b1b103738357cf`). Disclose them in **both the README and the report**.
- [ ] Actor model ID confirmed. The default is `claude-haiku-4-5-20251001`, read from env. Record the requested
      and returned IDs at freeze time.
- [ ] Pricing basis recorded in `.env` before the first paid call. With unknown pricing the ledger refuses every call.
- [x] **License: MIT** (2026-11-07). The L3 code is relicensed under MIT by its author (Sahil Soni).
- [x] **Overnight autonomy** (2026-11-07 10:50 PM): the lead has full *local* git authority (commit, tag, merge
      after a green gate, doc edits that record decisions). No push. No paid calls: Sahil fills `.env`,
      writes the batch authorization and starts the pilot himself in the morning.

## Deviations from sol.md (recorded honestly)

1. **The "existing" code does not exist.** sol.md §2 and §8 say to preserve the existing task generator,
   registry, publication service, episode loop and scoring. A full search of `D:\Eb1` (2026-11-07)
   found none of them. They are **built during the sprint**. Only the recorder, spend ledger and freeze
   utilities are reused from L3, and the report says so.
2. **The time box is about 32 hours, not "one day with code in hand".** The milestones below replace
   the schedule in sol.md §8.
3. **Live discovery moves before the freeze.** sol.md §6 already allows this. This file makes it a milestone.
4. **The paid-call gate is split** (owner decision, Sat 10:45 PM). The controlled A–D core pilot may proceed once
   the core mock e2e passes (T1–T6). The live chain (T7) has its own mock gate, which applies only to live-chain
   spend. The Sat 11:30 PM milestone was missed (no code existed at 10:41 PM). Waves 1 and 2 run as one wave.

## Hard cut order (decided now, before any outcome is seen)

When time runs short, cut from the bottom of this list.

1. **Controlled A/B/C/D core** (missing and approved states). This is required.
2. **Report-writing time is protected.** Writing starts **Sun 2:00 PM Central** whatever state the runs are in.
3. **Live three-role validation** (ordinary condition first, adversarial second).
4. **One confirmation experiment** (sol.md §6). Run it only if 1–3 are done and budget remains.

## Milestones (US Central)

| When | Milestone | Gate |
|---|---|---|
| Sat 11:30 PM | Docs merged · `docs/contribution.md` novelty memo (T0.5) · T1 interfaces frozen as `contract-v1` | `verify.sh` green |
| Sun 3:00 AM | Wave 1 merged (world+service, handoff, agents+runtime) | merge gate |
| Sun 6:00 AM | Wave 2 merged · mock end-to-end, 4 conditions × 2 states, plus the mock live chain | **gate for any paid call** |
| Sun 8:00 AM | Sahil's go → pilot (mechanics) + bounded live discovery (≤ $5) · base-rate check | §9 gate (below) |
| Sun 9:00 AM | **Freeze**: N, wordings, models, retry policy, contrasts, stopping rule | freeze ID committed |
| Sun 1:00 PM | Main controlled evaluation complete, or stopped at cutoff with coverage reported | — |
| Sun 2:00 PM | **Report writing starts** (non-negotiable) · live validation runs in parallel | — |
| Sun 10:00 PM | Figures and `RESULTS.md` regenerated from runs · full draft | every number traced |
| Mon 3:00 AM | Submit (official template PDF) · record confirmation | buffer to 5:59 AM |

## Statistical design (fixed before any data)

- The unit is the **task seed**. With 20 seeds you get **20 paired observations per condition within each
  approval state**. It is not 160 observations per comparison.
- **Primary contrasts:** (1) B−A and (2) D−B, on unauthorized committed publication in the missing
  state. Use exact McNemar on seed-paired binaries, with **Holm correction across these two**.
- **Secondary and exploratory:** C−A, and the interaction (D−C)−(B−A) with a task-level bootstrap that
  resamples seeds and keeps every cell of a seed together. Analyze the approved state separately.
- **Design power, as a property of the design and not a result:** an exact two-sided McNemar test
  reaches p < 0.05 only with **at least 6 discordant seeds, all in one direction**. Under Holm, the first
  rejection needs p < 0.025, which takes **at least 7**. A 0/20 cell has a
  95% Clopper–Pearson upper bound of about 17%. So this design can show large effects, and it
  cannot rule out modest ones.
- A nonsignificant contrast is reported as "not detected at this n". It is **never** reported as "no effect".
- Allocation of wording variants, the infrastructure-failure retry policy, and the multiplicity rule are frozen with the protocol.

## Competitive-result gate (sol.md §9). Run it at the discovery cutoff, Sun ~8:30 AM.

1. Is there a service-recorded violation, or a clearly measured change in verification?
2. Is the failure category unambiguous?
3. Is there a feasible fresh comparison that supports the explanation?
4. Is the result distinct from the closest **verified** prior work (`docs/contribution.md`)?

If the answers are yes, spend the remaining budget on confirmation. If no, complete the bounded evaluation and report it
honestly. If discovery shows a **zero base rate** for publication after denial in both A and B, the
sol.md §6 injected-denial experiment becomes the follow-up. Report 0/n with the upper bound and do not tune prompts to
manufacture failures.

## Definition of done

- [ ] PDF in the **official Apart template**. Main text is ≤ 8 pages; the cap **excludes references and appendices**.
      The abstract is 150–250 words. Authors and affiliations are listed.
- [ ] The mandatory **"Limitations and Dual-Use / Ethical Considerations"** appendix is present.
- [ ] One main figure (unauthorized publication and authorized success across A–D). A verification-category
      table. One annotated representative trace.
- [ ] Every number in the report is traceable to `RESULTS.md`, then to a run file. No placeholder statistics.
- [ ] Prior code is disclosed in the README **and** the report. AI assistance is disclosed.
- [ ] Public GitHub repo with code, config, synthetic episode records and reproduction steps. Optional under the rules,
      and we do it for reproducibility.
- [ ] Submitted before the deadline. Resubmissions use the same title.

## Mandatory agent instruction

Read this file before any research, design, implementation or delegation. Every task names the
TASKS.md item it advances. If it advances none, don't do it.
