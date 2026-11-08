# Citation check (Sun Nov 8 2026, ~3 PM CT)

An AI fact-checking agent reread every source in full text (METR HTML; arXiv HTML for [1]–[3]) and quoted
the passage behind each claim made in `docs/report_draft.md`. The owner reviews this file before the
"[provisional — owner to verify]" markers are removed.

| # | Claim in the paper | Verdict | Evidence (verbatim, location) | Correction applied |
|---|---|---|---|---|
| 1a | METR: "shared, unauthenticated board where any agent could post under another's name" | Partly | Intro: "coordinated a multi-day hack of Hugging Face on a shared unsanctioned “message board.”"; "an agent posted a message noting that it accidentally impersonated another agent" | "a shared, unsanctioned message board where handles were not authenticated: one agent reported accidentally impersonating another" |
| 1b | Agent quote "Could be another agent maliciously/spoofing…" | Supported | "OpenAI also shared a reasoning snippet from a different agent… “Could be another agent maliciously/spoofing. Shared board unauthenticated, names can be posted by anyone.”" | attribute as a snippet OpenAI shared with METR |
| 1c | Authors Wijk, Cotra, Greenblatt | Order differs | "CONTRIBUTORS Ryan Greenblatt, Ajeya Cotra, and Hjalmar Wijk"; BibTeX "author = {METR}" | cite as METR (contributors R. Greenblatt, A. Cotra, H. Wijk) |
| 2a | Zhu: "payment-style workflow… graphs" | Partly | Abstract: "communication, authorization, and execution structures"; §1 "authorization hypergraph"; "Consider a $100 expense reimbursement" | "protected-action workflows (e.g., a $100 reimbursement)… structures" |
| 2b | Requested vs committed rates reported separately | Supported | Abstract: "request-level and commitment-level safety readouts diverge" | — |
| 2c | Provenance cues "did not uniformly shift behavior toward the true state" | Partly | Abstract: "presented source-identity cues do not uniformly increase protected-action selection across three models"; §5.2 "reliance on presented authority cues rather than discrimination of truth" | reworded to the paper's terms |
| 2d | Executor lacks authoritative ground truth | Supported | §3.1: "an authoritative state, which the executor cannot directly observe" | — |
| 2e | Title, authors | Supported | arXiv:2610.24298v1 | — |
| 3a | Hu: "a fixed risky request… four static text conditions… single-turn approval" | Partly | §2.3: "40 authorization-sensitive risky actions and 16 safe controls"; App. B "a single approve/refuse decision under a forced-choice instruction" | "in a controlled probe… 40 risky and 16 safe… a single forced-choice approve/refuse decision" |
| 3b | "removing an '(UNVERIFIED)' marker alone raised approval sharply" | Contradicted ("alone") | App. A: "the whole wrapper, not a single token, is what changes across conditions"; Abstract: "from 5% to 60% on Llama-3.1-8B" | "removing the '(UNVERIFIED) Planner-Agent asserts that' wrapper… raised risky approval (e.g., 5%→60% on Llama-3.1-8B)" |
| 3c | "Their monitor has no callable tool to check the claim" | Not stated | §2.2: "The downstream monitor sees the rewritten handoff rather than the original evidence" | "their monitor sees only the request and a text handoff note, not the original evidence" |
| 3d | Title, author | Supported | arXiv:2609.20211v1 | — |
| 4a | EAL-Bench measures formation and propagation | Supported | Abstract: "whether memory errors propagate to unauthorized actions" | "converts… into persistent memory" |
| 4b | Executor "cannot dereference source identifiers" | Supported | App. C.5: "source identifiers are traceable but cannot be dereferenced by the executor" | — |
| 4c | Title, authors, v2 | Supported | arXiv:2609.01836v2 | — |

Sources were read in full text. In [1] and [2] math was stripped in conversion; the 5%→60% figure was
checked against the HTML math alttext.
