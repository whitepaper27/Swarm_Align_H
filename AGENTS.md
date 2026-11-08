# AGENTS.md

Instructions for every coding agent in this repo (Claude Code, Codex, Cursor, subagents, worktree
workers). Keep this file short and true. If an agent makes the same mistake twice, add one line under
**Lessons**.

## Project

- **What it is:** a research harness for the Apart **Swarm Alignment Hackathon** (Nov 6–8 2026, Track
  4). It measures whether peer approval claims change how a publisher agent verifies an exact approval
  tuple and acts on the answer. Plan: [`sol.md`](sol.md). **Read [`GOAL.md`](GOAL.md),
  [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`TASKS.md`](TASKS.md) before doing anything.**
- **Stack:** Python ≥ 3.10 with `uv`; `pytest`; `ruff`; the `anthropic` SDK for live calls; `matplotlib`.
  Model IDs, keys, prices and caps come from env (`.env.example`), never from code.
- **Layout:** `src/aiv/` (one subpackage per ARCHITECTURE §3 row) · `tests/` · `scripts/` (gate,
  `make_results.py`) · `protocol/draft/`, `protocol/frozen/<freeze_id>/` · `runs/<partition>/` ·
  `analysis/` (figures) · `docs/` (contribution memo, pilot notes, trace, decisions).
- **This repo stands alone.** It has no parent repo, and no other project's instructions apply here.

## Commands

| Purpose | Command |
|---|---|
| Install | `uv sync` |
| **The gate** | `./scripts/verify.sh` (ruff + full pytest) |
| Mock batch | `uv run aiv run --mode mock --partition mock --seeds 0-1` |
| Freeze | `uv run aiv freeze create` / `uv run aiv freeze validate <id>` |
| Results | `uv run python scripts/make_results.py` (writes `RESULTS.md`) |
| Live (owner only) | `uv run aiv run --mode live --confirm-spend --batch <authorized-batch-id> …` |

## How to work

1. Name the TASKS.md ID you are working on. If no task covers it, stop and ask.
2. Stay inside your worktree's modules (ARCHITECTURE §3). `contract.py` changes go through the lead and a new contract tag.
3. Write the test first for anything under ARCHITECTURE §12 (invariants).
4. Small diffs, one concern each. Run `./scripts/verify.sh` before saying "done". Paste the result line.
5. Report honestly. If something failed, say what failed and paste the output.

## Hard rules

- **No live or paid model call** unless Sahil wrote a batch authorization **and** the command has
  `--confirm-spend`. Never create, edit or forge an authorization file. Mock mode is the default everywhere.
- **Never write a result number by hand** into `RESULTS.md`, the report, the README or docs.
  Numbers come from `scripts/make_results.py` reading run files. Mock-derived output is always labeled **MOCK**
  and is never presented as a result.
- **Outcomes come from service and registry events (`seq`-ordered), never from agent text or claims.**
- **Never retry a refusal or an invalid decision.** Only `infra_error` is retried, under the frozen policy, with state reset.
- **Never pool pilot or discovery runs** into confirmatory estimates. Never stop a frozen batch early because results look good.
- **Frozen protocol:** never edit `protocol/frozen/`. A change means `aiv freeze create` → new freeze
  ID, and the old runs stay in their own stratum.
- **Tests:** never weaken an assertion, skip a test, or loosen a check just to make it pass. If a test is
  *demonstrably wrong*, fix it in its own commit and justify the fix in the message (what was wrong, and the evidence).
- **Claims:** never write "first", "novel", "collusion", "nobody has", or any citation that isn't in
  `docs/contribution.md` with a URL that was actually opened.
- **Secrets:** never commit `.env`, keys, or private data. Run files contain synthetic data only.
- **Dependencies:** adding one requires a line in the commit message: why it is needed, and why the stdlib or existing deps can't do it.
- **Reused L3 code** keeps its `# Adapted from Agent_World_L3 @61c7d37 …` header. Don't copy other L3 files
  without adding them to ARCHITECTURE §13.

## Git

- `main` only moves through lead merges after a green gate. Workers use `git worktree` branches
  `w<N>-<module>`.
- Commit only when your task says so. End messages with the `Co-Authored-By` trailer your harness provides.

## Lessons

- Bash heredocs in this harness turn a written `\\n` into a real newline inside Python source. Write patch scripts and test files with the Write/Edit tools, not heredocs.
- A test that calls `cli.main` or `freeze` without `--repo-root <tmp_path>` writes into the real repo (it once created a stray `protocol/frozen/`). Always point the CLI at `tmp_path`.
