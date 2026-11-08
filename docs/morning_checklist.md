# Checklist for Sahil (Sun Nov 8)

Status when you stopped (Sun ~12:30 AM CT):
- Pilot and discovery are done. Total spend so far is in `.spend/ledger.json`; the per-run details are
  in `docs/pilot.md` (generated).
- `.env` is complete: key, model, prices, caps, recorder key, spend root.
- Your three decisions are recorded in `sol.md §14` and GOAL deviations 5–6: **N = 100**, report the
  adversarial worker's refusals (no prompt search), and build T13.
- The main freeze config is ready: `protocol/freeze_config_main.json` (seeds 0–99, Haiku 4.5).

Run everything from `D:\Eb1\swarm-alignment-2026`. In Claude Code, prefix each line with `!`.

## 1. Freeze (T10): 2 min, from a clean tree

```bash
git status --short                       # must be empty
uv run aiv freeze create --config protocol/freeze_config_main.json --note "main N=100"
uv run aiv freeze validate <freeze_id>   # prints OK
git add protocol/frozen && git commit -m "Freeze <freeze_id> (main, N=100)"
```

## 2. Authorize main and live (only you)

```bash
printf '{"batch_id": "main-1", "partition": "main", "max_usd": 25, "authorized_by": "Sahil Soni", "created": "2026-11-08T09:00-06:00"}\n' > protocol/authorizations/main-1.json
printf '{"batch_id": "live-1", "partition": "live", "max_usd": 8, "authorized_by": "Sahil Soni", "created": "2026-11-08T09:00-06:00"}\n' > protocol/authorizations/live-1.json
```

The `SPEND_CAP_*` values in `.env` are the enforced limits. The authorization file only grants permission.

## 3. Main evaluation (T11): 800 episodes

```bash
uv run aiv run --mode live --partition main --seeds 0-99 --freeze-id <freeze_id> --workers 6 --confirm-spend --batch main-1
```

The CLI refuses the run if the seeds, conditions or states differ from the freeze, or if `protocol/draft/`
differs from the frozen copy. **Don't edit `protocol/draft/` while it runs**: the prompt is re-read for
every episode.

## 4. Live validation (T12): ordinary first, then adversarial

```bash
uv run aiv run --mode live --partition live --seeds 300-311 --chain ordinary --freeze-id <freeze_id> --workers 4 --confirm-spend --batch live-1
uv run aiv run --mode live --partition live --seeds 320-331 --chain adversarial --freeze-id <freeze_id> --workers 4 --confirm-spend --batch live-1
```

Then label every false approval claim by hand (worker-originated / coordinator-introduced /
coordinator-strengthened / ambiguous), quoting the evidence. Do this **before** computing any summary.

## 5. Results, traces and the T13 handoff

```bash
uv run python scripts/rescore.py runs/main/* runs/live/*      # only if scoring changed after the run
uv run python scripts/make_results.py --partitions main,live
uv run python scripts/render_trace.py runs/main/<run_id> --list
uv run python scripts/render_trace.py runs/main/<run_id> --episode <id> --out docs/trace.md
```

- **T13 (approved):** the agent builds it in a separate worktree while step 3 runs, then mock-tests it.
  Freeze it as F2 and run it in the `confirm` partition with its own authorization (`confirm-1`, about $5).
- **Report:** `docs/report_draft.md` has `{{RESULT}}` slots that map to sections of `RESULTS.md`. Writing
  starts at 2 PM whatever state the runs are in. Port it into the official Apart template (link at the top
  of the draft; nobody has opened the doc yet).

## Still waiting on your review
1. `docs/contribution.md`: check each "[provisional]" comparison against the paper before citing it.
2. The publisher prompt trim and the near-miss version line (sol.md §13). They're frozen at step 1.
