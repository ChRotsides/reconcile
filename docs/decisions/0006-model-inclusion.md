# 0006. Model inclusion

**Status:** accepted as a deferral, 2026-11-02. No model code exists in the repository.

## Context
`SCOPE.md` says model assistance is optional and kept only if it adds a measured benefit over a no-model baseline. `PLAN.md` M4 (bounded AI assistance) comes after the M3 protocol freeze.

## Decision
- Build and measure the deterministic investigator first. It already runs end to end with no credentials, inside a network-less container (M2 gate).
- Defer any model use to M4, and only under the M3 frozen protocol: a validation-only provider comparison, strict output schemas, per-case budgets, citation and calculation validation, and the declared ablations (`PLAN.md` M4 and section 7).
- Start M4, if it happens, with explanation drafting from the deterministic packet. Diagnosis or retrieval by a model is tried only against a specific measured validation failure.
- No model call touches money: every reported calculation is recomputed deterministically, and returned IDs must resolve in the snapshot.
- No provider SDK, API key or model prompt exists today, so no practitioner or benchmark data has been sent to a model.

## Alternatives considered
1. Add a model now to generate explanations (the simplest "feature" option): rejected. Without a frozen protocol and cost/quality criteria, any benefit would be unmeasured, and attractive prose is not evidence (`PLAN.md` M4 gate).
2. Use a model for retrieval or diagnosis from the start: rejected. On the development validation split, the deterministic investigator's 119/123 correct-and-complete already exceeds the observable-input classifier's 109/123 (`docs/baseline-observable.md` section 3); that is not evidence about what a model would add, but it means any model must be compared against a strong deterministic baseline, and the comparison needs a protocol first.
3. Deterministic first, model deferred and conditional: chosen.

## Evidence
`docs/dev-results.md` section 14; `docs/baseline-observable.md` section 3; `PLAN.md` M2 gate outcome and M4; `SCOPE.md` last paragraph. Provenance: D047, D099, D109.

## Consequences and limits
- The README cannot claim AI value. The release may be a deterministic tool only; `PLAN.md` accepts that outcome.
- This record is a deferral, not a finding that models are not useful.

## Revisit if
The M3 protocol is frozen and the error inventory shows a failure class that a bounded model step could address, or the observed errors and abstentions turn out to be concentrated in free-text cases the deterministic rules cannot read. A model is then added only if it passes the M4 gate (quality and cost, measured on held-out data) and is removed if it does not.
