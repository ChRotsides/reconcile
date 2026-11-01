# Reconcile

An open-source, evidence-backed investigator for payment reconciliation exceptions.

**Status:** Planning only. No application, benchmark results, customer savings, or external adoption have been achieved.

## Problem and first release

When a payment does not reconcile, finance operations teams must assemble records from accounting and banking systems to determine what happened. Reconcile will return a supported diagnosis, the relevant records, exact calculations and a proposed next step—or identify the missing evidence.

Start with suspected duplicate payments, missing settlements and bank/accounting amount differences. Include legitimate pending payments and fees so the system can distinguish a real exception from ordinary activity.

The first release is a read-only Python library and CLI with JSON/HTML evidence reports. It accepts structured operational exports. Deterministic code handles financial arithmetic and relationship checks; model assistance is an experiment whose contribution must be measured.

## Proof of value

Compare against strong SQL/classical-ML baselines with deterministic evidence retrieval on an independent public benchmark. Measure correct diagnoses with complete evidence, false alarms, abstentions, cost and latency. Keep answer labels inaccessible during inference and publish failure cases.

Evaluation belongs in the project from the beginning. Public synthetic results can establish bounded technical value; measured analyst time savings require a later workflow study.

## Build documentation

- [Full build plan](PLAN.md): architecture, contracts, implementation milestones, evaluation, testing, release gates and effort estimates.
- [Scope](SCOPE.md): first-release boundaries and acceptance criteria.

This plan replaces the earlier PDF-matching and approval application. Begin with the dataset and baseline viability milestone; expand only after useful value is demonstrated.
