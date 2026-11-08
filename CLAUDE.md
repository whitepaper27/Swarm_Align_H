# CLAUDE.md

Read **[`AGENTS.md`](AGENTS.md)** first (rules + commands), then **[`GOAL.md`](GOAL.md)** (deadline,
cut order, owner decisions), then the task you were given in **[`TASKS.md`](TASKS.md)**. The plan is
[`sol.md`](sol.md); the design is [`ARCHITECTURE.md`](ARCHITECTURE.md).

Three rules that matter most:
1. **No live/paid calls** without Sahil's batch authorization + `--confirm-spend`. $50 total cap.
2. **No hand-written result numbers anywhere.** `RESULTS.md` is generated from run files.
3. **Outcomes come from service events, never agent claims.** Don't call it "collusion".

Windows host: prefix live Python with `PYTHONIOENCODING=utf-8`. Deadline: Sun Nov 8 11:59 PM AoE
= Mon Nov 9 5:59 AM Central.
