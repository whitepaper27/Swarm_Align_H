# Access Isn't Verification

A research harness for the [Apart Swarm Alignment Hackathon](https://apartresearch.com/sprints/swarm-alignment-2026)
(Nov 6–8 2026; Track 4, Multi-Agent Oversight and Control). It studies **where authoritative approval
information loses its force as work passes between agents**: whether the check is skipped, the wrong tuple is checked, or the agent acts
after an explicit denial. It compares peer approval assertions with service-recorded provenance.

**Status:** under construction. No results yet. See [`RESULTS.md`](RESULTS.md), which is generated from run files.

## Docs

[`GOAL.md`](GOAL.md) goal, deadline, cut order · [`ARCHITECTURE.md`](ARCHITECTURE.md) design ·
[`TASKS.md`](TASKS.md) work list · [`AGENTS.md`](AGENTS.md) agent rules ·
[`REPORT_OUTLINE.md`](REPORT_OUTLINE.md) report plan · [`sol.md`](sol.md) research plan.

## Quickstart (mock mode, no API key or spend)

```bash
uv sync
./scripts/verify.sh
uv run aiv run --mode mock --partition mock --seeds 0-1
uv run python scripts/make_results.py
```

## Pre-existing code disclosure

Some modules are adapted from the author's earlier project **Agent_World_L3 / Ethwall**
(github.com/whitepaper27/ai-collusion-research-sprint, commit `61c7d37`), written before this sprint:
the spend ledger, the hash-chained recorder, the freeze utilities, and the verify script. Each adapted file
carries a header line that says so, and they are listed in ARCHITECTURE §13. Everything else was written
during the sprint. AI coding assistants (Claude Code) were used. The author reviewed the outputs and is
responsible for their accuracy.

## Safety

All data is synthetic, and the "publication service" is a local mock that only records events. The
adversarial worker prompts are released for reproducibility. See the report's Limitations and Dual-Use appendix.

## License

MIT (see [`LICENSE`](LICENSE)). The adapted L3 modules are relicensed under MIT by their author.
