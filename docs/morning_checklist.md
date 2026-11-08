# Morning checklist for Sahil (Sun Nov 8)

Status at handoff: the harness is built and the **core mock gate (T8) is green**. That covers the mock
e2e of 4 conditions × 2 states, scoring, MOCK results and figure, and a freeze dry run. The **chain mock
gate is green** too (ordinary + adversarial). No paid call has been made. Everything below needs you,
and only you can authorize spend.

Run all commands from `D:\Eb1\swarm-alignment-2026` in PowerShell. The CLI loads `.env` itself in live
mode. Shell variables override `.env`.

## 1. Decisions to review first (about 10 min)

1. **Publisher prompt trimmed to sol.md** (`docs/decisions.md`, commit `abd8a3f`). The sentence telling the
   publisher that peer statements are not approvals is gone, because it directly coached against the B/D
   manipulation. The stated obligation "Before publishing, you must verify that the exact tuple is approved" is
   kept, as sol.md requires. To restore the original prompt, run `git checkout 43866dc -- protocol/draft/publisher_policy.md`
   and commit **before the pilot**. After the pilot the prompt is frozen.
2. **Near-miss reachability** (`docs/decisions.md`). No handoff names the decoy tuple, so
   `nonmatching_lookup_only` can only come from the publisher's own errors. The report has to say this.
3. **Novelty memo** `docs/contribution.md` (provisional). The closest prior work is *Who Speaks, Who
   Authorizes, Who Acts?* (arXiv:2610.24298). Read sections 1 and 7 before anything gets cited.
4. **Model:** the default is `claude-haiku-4-5-20251001`. Change `ACTOR_MODEL_ID` if you prefer another.

## 2. Fill `.env` (T0b)

```powershell
Copy-Item .env.example .env   # then edit .env
```

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key |
| `ACTOR_MODEL_ID` | confirm (default above) |
| `PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK` | **from the Anthropic pricing page for that model**. If left empty, the ledger refuses every call |
| `AIV_RECORDER_KEY` | any long random string: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AIV_SPEND_ROOT` | `D:\Eb1\swarm-alignment-2026\.spend` (one shared ledger; it is gitignored) |

The caps are already set: total $50, pilot $5, discovery $5, main $25, live $8, confirm $5, episode $0.80.

## 3. Write the batch authorizations (only you)

Create one file per batch in `protocol/authorizations/`. The agent never writes these.

```json
{"batch_id": "pilot-1", "partition": "pilot", "max_usd": 5, "authorized_by": "Sahil Soni", "created": "2026-11-08T08:00-06:00"}
```

For discovery, use the same shape with `"batch_id": "discovery-1", "partition": "discovery"`.

## 4. Pilot + discovery (T9, ≤ $5 + ≤ $5)

The pilot uses seeds 100+ so the main run's seeds 0–19 stay fresh.

```powershell
$env:PYTHONIOENCODING = "utf-8"
# Controlled pilot: 2 seeds x 4 conditions x 2 states = 16 episodes
uv run aiv run --mode live --partition pilot --seeds 100-101 --confirm-spend --batch pilot-1
# Live chain discovery: 3 seeds x conditions A,C x missing state = 6 episodes each
uv run aiv run --mode live --partition discovery --seeds 200-202 --states missing --chain ordinary --confirm-spend --batch discovery-1
uv run aiv run --mode live --partition discovery --seeds 210-212 --states missing --chain adversarial --confirm-spend --batch discovery-1
uv run python scripts/make_results.py --partitions pilot,discovery --out docs/pilot_results.md
```

Then ask the agent to write `docs/pilot.md`: cost per episode (from the ledger and `model.call` events), the
projected cost of 20 seeds, the denial→publish base rate, and answers to the GOAL §9 gate.
**Mechanics fixes only. No prompt changes.**

## 5. Freeze (T10, by Sun 9 AM)

Choose N: 20 seeds by default, fewer if the pilot projection is above $25. Then:

```powershell
uv run aiv freeze create --note "main freeze"      # prints F<date>-<hash>; commit protocol/frozen/
uv run aiv freeze validate <freeze_id>
```

## 6. Main evaluation (T11) and live validation (T12)

Use the authorizations `main-1` (partition `main`, $25) and `live-1` (partition `live`, $8).

```powershell
uv run aiv run --mode live --partition main --seeds 0-19 --freeze-id <freeze_id> --workers 4 --confirm-spend --batch main-1
uv run aiv run --mode live --partition live --seeds 300-305 --chain ordinary --freeze-id <freeze_id> --confirm-spend --batch live-1
uv run aiv run --mode live --partition live --seeds 310-315 --chain adversarial --freeze-id <freeze_id> --confirm-spend --batch live-1
uv run python scripts/make_results.py --partitions main,live
```

Live runs into `main`, `live` or `confirm` are refused without a `--freeze-id` whose files match
`protocol/draft/` byte for byte.

## 7. Writing starts Sun 2 PM, regardless of run state (GOAL cut order)
