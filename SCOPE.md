# Reconcile: First-Release Scope

**Status:** Planned. Follow [PLAN.md](PLAN.md) for implementation tasks and decision gates.

## User and outcome

A finance operations analyst needs to understand an accounts-payable-to-bank exception. A developer needs a reusable investigator that returns a diagnosis with checkable records and calculations.

The initial objective is a reproducible improvement over a strong baseline in correct, evidence-complete investigation. Customer savings and external adoption remain unmeasured.

## Included

| Area | First-release boundary |
|---|---|
| Cases | Suspected duplicate payments, missing settlements and bank/ERP amount differences, plus relevant legitimate cases |
| Input | One organization snapshot of structured operational records, an entity identifier and an investigation cutoff |
| Data | One primary FinRCA adapter and a documented audit of the independent synthetic benchmark |
| Retrieval | Bounded observable relationships and conservative candidate search |
| Checks | Exact money, chronology, allocations and settlement policy |
| Output | Diagnosis or abstention, evidence, calculations, proposed action and missing information |
| AI | Optional structured assistance compared with a no-model baseline; no operational write tools |
| Interface | Python library, CLI, batch execution and static JSON/HTML reports |
| Evaluation | Strong baselines, isolated labels, held-out groups, evidence quality, false alarms, cost, latency and ablations |
| Operation | Versioned snapshots, atomic artifacts, bounded retries, compatible resume/cache and redacted logs |

Unsupported failure families and currencies must be visibly deferred. Insufficient evidence must not become a legitimate/no-failure conclusion. Import deduplication must preserve distinct transactions even when their amounts and dates match.

## Deferred

PDF/OCR extraction, hosted UI, multi-user tenancy, live banks, ERP write-back, approvals, payment execution, streaming infrastructure, broad agent orchestration and a separate evaluation product. Add datasets and connectors after the first useful comparison rather than expanding version 0.1.

## Release acceptance

- Supported cases run end-to-end in deterministic mode without credentials.
- Missing evidence, ambiguity, unsupported scope and execution failure have explicit outcomes.
- Accepted references resolve within the permitted snapshot; reported financial calculations are independently checked.
- Inference cannot access answer labels, generator provenance or oracle-selected evidence packets.
- Comparisons use equal observable information, strong baselines and a frozen protocol.
- Results disclose denominators, failed slices, abstention, cost, latency and uncertainty.
- Interruption, repeated imports, malformed model output and budget exhaustion have tested failure/recovery behavior.
- The documented demo runs in a clean environment, with fixture/replay results distinguished from live-model results.
- README claims match measured outcomes and dataset limitations.

Keep model-assisted functionality only when it adds a useful measured benefit. A negative result may justify a simpler deterministic release or stopping expansion; a larger interface must not conceal it.
