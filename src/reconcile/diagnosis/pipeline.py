"""Entry point: `investigate(snapshot_path, request) -> EvidencePacket`.

Inference sees the prepared snapshot and the request only. Every request ends
in an explicit disposition (PLAN.md section 4).
"""

from pathlib import Path

from reconcile.diagnosis.invoice import diagnose_invoice
from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    InvestigationRequest,
)
from reconcile.retrieval.invoice import gather_invoice
from reconcile.retrieval.snapshot import HOPS, Limits, Reader, RetrievalIncomplete

INVESTIGATOR = "invoice_primary_v1"
DEFAULT_LIMITS = Limits()
# Tables whose primary-key column is known, to tell "absent" from "unsupported".
KEY_COLUMNS = {
    "invoices": "invoice_id",
    "payments": "payment_id",
    "bank_transactions": "bank_transaction_id",
    "gl_entries": "journal_id",
}


def _packet(
    request: InvestigationRequest,
    provenance: tuple[tuple[str, str], ...],
    disposition: Disposition,
    reason: str,
    missing: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> EvidencePacket:
    diagnosis = Diagnosis(disposition, None, reason=reason, missing_information=missing)
    return EvidencePacket(
        request, diagnosis, limitations=limitations, provenance=provenance
    )


def investigate(
    snapshot_path: Path,
    request: InvestigationRequest,
    limits: Limits = DEFAULT_LIMITS,
) -> EvidencePacket:
    prov = (
        ("investigator", INVESTIGATOR),
        ("max_hops", str(HOPS)),
        ("max_rows", str(limits.max_rows)),
        ("max_seconds", str(limits.max_seconds)),
    )
    entity = request.primary_entity
    try:
        with Reader(snapshot_path, limits) as reader:
            key = KEY_COLUMNS.get(entity.table)
            if key is not None and not reader.exists(
                entity.table, key, entity.record_id
            ):
                return _packet(
                    request,
                    prov,
                    Disposition.ABSTAINED,
                    "primary_entity_not_in_snapshot",
                    ("the primary record, absent from the snapshot at the cutoff",),
                )
            if entity.table != "invoices":
                return _packet(
                    request,
                    prov,
                    Disposition.OUT_OF_SCOPE,
                    "entity_type_not_supported_yet",
                    limitations=(
                        "This investigator handles invoice-primary requests only.",
                    ),
                )
            facts = gather_invoice(reader, entity.record_id, request.cutoff)
    except RetrievalIncomplete as e:
        return _packet(
            request,
            prov,
            Disposition.ABSTAINED,
            e.reason,
            ("complete retrieval within the declared limits",),
            ("Retrieval hit a limit; the evidence is incomplete, not absent.",),
        )
    try:
        return diagnose_invoice(request, facts, prov)
    except ValueError as e:  # malformed row, inconsistent packet: explicit failure
        return _packet(
            request,
            prov,
            Disposition.FAILED,
            "malformed_record",
            limitations=(f"Investigation failed: {e}",),
        )
