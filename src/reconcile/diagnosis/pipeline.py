"""Entry point: `investigate(snapshot_path, request) -> EvidencePacket`.

Inference sees the prepared snapshot and the request only. Every request ends
in an explicit disposition (PLAN.md section 4).
"""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from reconcile.diagnosis.invoice import diagnose_invoice
from reconcile.diagnosis.ledger import FieldMissing
from reconcile.diagnosis.payment import diagnose_bank_row, diagnose_payment
from reconcile.diagnosis.policy import declared
from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    InvestigationRequest,
)
from reconcile.retrieval.invoice import gather_invoice
from reconcile.retrieval.snapshot import HOPS, Limits, Reader, RetrievalIncomplete

MANIFEST_FILE = "manifest.json"  # written beside the snapshot by preparation
INVESTIGATOR = "deterministic_v2"
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


class _Failure(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _check_snapshot(path: Path, request: InvestigationRequest) -> tuple[str, ...]:
    """Bind the file to the request through the manifest written beside it and
    return the data-coverage limitations the manifest declares (none for FinRCA).

    Trusts the manifest; the content digest is not recomputed (assumption).
    """
    try:
        manifest = json.loads((path.parent / MANIFEST_FILE).read_text())
        digest = manifest["snapshot"]["content_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise _Failure("snapshot_manifest_unreadable", f"{type(e).__name__}") from e
    if digest != request.snapshot:
        raise _Failure("snapshot_mismatch", "request names a different snapshot")
    notes: list[str] = []
    missing = manifest.get("tables_not_supplied")
    if isinstance(missing, list) and missing:
        notes.append(
            "These tables were not supplied with this data, so a search of them "
            "finding nothing says nothing about them: "
            f"{', '.join(str(t) for t in missing)}."
        )
    through = manifest.get("bank_data_through")
    if through:
        notes.append(
            f"Bank data was declared complete through {through} by whoever "
            "prepared the data."
        )
    return tuple(notes)


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
        ("requested_policy_version", request.policy_version),
        *declared(),
    )
    try:
        notes = _check_snapshot(snapshot_path, request)
        packet = _investigate(snapshot_path, request, limits, prov)
        return replace(packet, limitations=(*packet.limitations, *notes))
    except _Failure as e:
        return _failed(request, prov, e.reason, str(e))


def _investigate(
    snapshot_path: Path,
    request: InvestigationRequest,
    limits: Limits,
    prov: tuple[tuple[str, str], ...],
) -> EvidencePacket:
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
            if entity.table == "payments":
                packet = diagnose_payment(request, reader, prov)
            elif entity.table == "bank_transactions":
                packet = diagnose_bank_row(request, reader, prov)
            elif entity.table == "invoices":
                facts = gather_invoice(reader, entity.record_id, request.cutoff)
                packet = diagnose_invoice(request, facts, prov, reader)
            else:
                return _packet(
                    request,
                    prov,
                    Disposition.OUT_OF_SCOPE,
                    "entity_type_not_supported_yet",
                    limitations=(
                        "This investigator handles invoice, payment and "
                        "bank-transaction requests only.",
                    ),
                )
            if packet.diagnosis.disposition is Disposition.ANSWERED:
                reader.check_time()  # the budget covers diagnosis too
            return packet
    except RetrievalIncomplete as e:
        return _packet(
            request,
            prov,
            Disposition.ABSTAINED,
            e.reason,
            ("complete retrieval within the declared limits",),
            ("Retrieval hit a limit; the evidence is incomplete, not absent.",),
        )
    except sqlite3.Error as e:
        return _failed(request, prov, "snapshot_unreadable", type(e).__name__)
    except FieldMissing as e:
        return _packet(
            request,
            prov,
            Disposition.ABSTAINED,
            "required_field_missing",
            (f"the value of {e.column}, absent from the data",),
            (f"The data has no value for {e.column}; the check needs it.",),
        )
    except ValueError as e:  # malformed or non-unique row, inconsistent packet
        return _failed(request, prov, "malformed_record", str(e))


def _failed(
    request: InvestigationRequest,
    prov: tuple[tuple[str, str], ...],
    reason: str,
    detail: str,
) -> EvidencePacket:
    return _packet(
        request,
        prov,
        Disposition.FAILED,
        reason,
        limitations=(f"Investigation failed: {detail}",),
    )
