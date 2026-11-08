# REPORT_OUTLINE

Target: the official Apart template (Google Doc linked from the sprint Guidelines tab). The main text is **≤ 8 pages,
excluding references and appendices**. Abstract **150–250 words**. Authors and affiliations are listed.
Rubric: Impact & Innovation · Execution Quality · Presentation & Clarity (each 1–5).

Working title: **Access Isn't Verification**. Sharpen it only if the data justify a sharper title.

| § | Section | Pages | Rubric axis it serves | Source |
|---|---|---|---|---|
| — | Abstract: measured findings only | — | Clarity | RESULTS.md |
| 1 | Introduction: the swarm incident motivation (read sources only); approval claims as the weak link in agent handoffs; the contribution vs closest verified work | 1 | Impact | docs/contribution.md |
| 2 | Related work: Track 4 readings + the verified close work, with method-level differences | 0.5 | Impact | docs/contribution.md |
| 3 | Setup: exact tuple, near misses, registry/service as the source of truth, conditions A–D, provenance design, verification categories | 1.5 | Execution | ARCHITECTURE.md |
| 4 | Protocol: freeze, N, models, retry/stopping rules, statistics (McNemar + Holm, seed bootstrap, exact CIs), power limits | 0.75 | Execution | GOAL.md, freeze manifest |
| 5 | Results: **Figure 1** (unauthorized publication + authorized success across A–D, both states); verification-category table; primary contrasts | 1.75 | Execution, Clarity | RESULTS.md |
| 6 | Live three-role validation + **one annotated trace** | 1 | Impact | docs/trace.md |
| 7 | Mitigation vs utility (provenance; enforcement reference labeled "expected by construction") | 0.5 | Impact | RESULTS.md |
| 8 | Discussion & conclusion: what generalizes, what doesn't | 0.5 | Clarity | — |
| — | References | excl. | — | verified URLs only |
| App. A | **Limitations and Dual-Use / Ethical Considerations** (mandatory) | excl. | — | see below |
| App. B | Prompts, wording variants, freeze manifest, reproduction steps | excl. | Execution | protocol/frozen |
| App. C | AI-assistance + pre-existing-code disclosure (L3 @61c7d37, modules listed) | excl. | — | README |

## Appendix A must cover

- The claims are limited to the tested models, prompts, workflow and budget. Three agents say nothing about behavior at population scale.
- Scripted assertions do not establish emergent collusion. Live false claims do not establish knowing conspiracy.
  A provenance effect does not prove a unique psychological mechanism.
- The n is small (paired seeds per state), and modest effects cannot be ruled out.
- Dual use: the adversarial worker prompts are a recipe for inducing unauthorized actions. Release them with
  context; the mitigation is service-side exact-tuple enforcement.
- Synthetic data only. No real publication endpoint. No real persons or organizations.

## Pre-submit checklist

- [ ] Every number in the PDF matches `RESULTS.md`, and each has a run file behind it.
- [ ] Every citation was opened and checked. No "first" or "novel" without `docs/contribution.md` support.
- [ ] The word "collusion" is used only for prior work or as an explicit non-claim.
- [ ] Abstract word count is 150–250. Main text is ≤ 8 pages.
- [ ] Appendix A is present. Prior-code and AI-assistance disclosures are present.
- [ ] The repo link works. README reproduction steps were run on a clean clone in mock mode.
