# 0001. Local, read-only scope

**Status:** accepted, 2026-11-02 (summarises M0 to M2).

## Context
The tool investigates payment exceptions for an analyst. A tool that could write to an ERP or bank, or that served several organisations, would need security and approval work that `SCOPE.md` defers.

## Decision
- One organisation snapshot per local run (`PLAN.md` section 1). No hosted service, tenancy, live bank or ERP connection.
- The investigator only reads. It opens the snapshot with SQLite `mode=ro` and `PRAGMA query_only = ON` (`src/reconcile/retrieval/snapshot.py`), has no write, payment or free-form SQL/shell tool, and returns proposed actions only.
- The inference container runs with `--network none --read-only --cap-drop ALL` and three mounts (`deploy/inference/`, `docs/isolation.md`).

## Alternatives considered
1. Do nothing special and let the code open the database normally: rejected. Nothing would stop a bug or a future model tool from modifying evidence.
2. Build for hosted, multi-user use from the start: rejected. `SCOPE.md` "Deferred" lists tenancy, authentication, write-back and payment execution; the plan says not to advertise hosted multi-tenant security (`PLAN.md` section 1).

## Evidence
- `tests/unit/test_finrca_adapter.py::test_outputs_are_read_only` (prepared snapshot files are not writable; a `DELETE` through a read-only connection fails).
- `tests/integration/test_inference_container.py` (absence check and byte-identical output; skipped without Docker).
- `docs/isolation.md` states what the container proves and what it does not.
- Provenance: D001, D099.

## Consequences and limits
- Read-only is enforced for the investigator's database access and the container flags. The container tests were run on one machine; Docker's enforcement of the flags and the unpinned base image are assumptions (A048 in the log, `docs/isolation.md` "Still an assumption").
- Nothing here has been tested with real exports or at scale.

## Revisit if
A user needs a connector or a hosted deployment; that is a new scope decision, not a change to this record.
