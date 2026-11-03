# 0006. Model inclusion

**Status:** accepted, 2026-11-03 (M4 gate). Model drafting was built, measured and removed. The product makes no model calls; the code is at the git tag `m4-drafting-experiment`.

## Context
`SCOPE.md` says model assistance is optional and kept only if it adds a measured benefit over a no-model baseline. `PLAN.md` M4 (bounded AI assistance) comes after the M3 protocol freeze.

## Decision
- The product is deterministic. No provider SDK, API key handling or prompt remains on `main`.
- M4 tested one thing: explanations drafted from finished packets, which cannot change a diagnosis. Model adjudication was not attempted, because the investigator's remaining errors are label-ambiguous swaps with no operational consequence.
- Drafting was removed because the frozen protocol requires a recorded practitioner benefit (none exists) and zero unsupported claims (an audit confirmed a problem in 8 of 30 accepted drafts).

## Alternatives considered
1. Add a model now to generate explanations (the simplest "feature" option): rejected. Without a frozen protocol and cost/quality criteria, any benefit would be unmeasured, and attractive prose is not evidence (`PLAN.md` M4 gate).
2. Use a model for retrieval or diagnosis from the start: rejected. On the development validation split, the deterministic investigator's 119/123 correct-and-complete already exceeds the observable-input classifier's 109/123 (`docs/baseline-observable.md` section 3); that is not evidence about what a model would add, but it means any model must be compared against a strong deterministic baseline, and the comparison needs a protocol first.
3. Deterministic first, model deferred and conditional: chosen, and tested in M4.
4. Keep drafting off by default, pending review: rejected. The frozen protocol has no such exception, and the checks cannot establish that a sentence is true.

## Evidence
`docs/m4-trial-plan.md`; `docs/m4-results.md` sections 3 and 6a; `evaluation/protocol.toml` `[model_assistance]`; `PLAN.md` M4 gate outcome; `docs/dev-results.md` section 14; `docs/baseline-observable.md` section 3. Provenance: D047, D099, D109, D148 to D155.

## Consequences and limits
- The README cannot claim AI value. The release is a deterministic tool, which `PLAN.md` accepts.
- This is a result about one design on synthetic development data, audited by AI reviewers, not finance experts. It is not a finding that models can never help.

## Revisit if
A practitioner review records a need that templates cannot meet. Any new design must render factual text from checked statements, start from the review findings listed in `docs/m4-results.md`, and needs a new protocol version.
