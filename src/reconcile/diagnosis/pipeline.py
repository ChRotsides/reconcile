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
from reconcile.diagnosis.payment import (
    F12,
    PENDING,
    diagnose_bank_row,
    diagnose_payment,
)
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


def _check_snapshot(
    path: Path, request: InvestigationRequest
) -> tuple[tuple[str, ...], bool, str | None]:
    """Bind the file to the request through the manifest written beside it and
    return the data-coverage limitations the manifest declares (none for FinRCA),
    whether fee journals are recognisable in this data (true unless the manifest
    says `fee_journals_recognised: false`; FinRCA has no such key) and the first
    day the bank data covers (`bank_data_from`, absent for FinRCA).

    The request's cutoff must be the one the data was prepared with: rows after
    it are absent, rows before it were all kept, and some queries have no date
    bound, so a later or earlier cutoff would read a different cut of the data.

    Trusts the manifest; the content digest is not recomputed (assumption).
    """
    try:
        manifest = json.loads((path.parent / MANIFEST_FILE).read_text())
        digest = manifest["snapshot"]["content_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise _Failure("snapshot_manifest_unreadable", f"{type(e).__name__}") from e
    if digest != request.snapshot:
        raise _Failure("snapshot_mismatch", "request names a different snapshot")
    prepared = manifest.get("cutoff")
    if prepared is not None and prepared != request.cutoff.isoformat():
        raise _Failure(
            "cutoff_differs_from_snapshot",
            f"the request's cutoff {request.cutoff.isoformat()} differs from the "
            f"cutoff {prepared} the data was prepared with; prepare the data again "
            "with the cutoff to investigate",
        )
    notes: list[str] = []
    missing = manifest.get("tables_not_supplied")
    if isinstance(missing, list) and missing:
        notes.append(
            "These tables were not supplied with this data, so a search of them "
            "finding nothing says nothing about them: "
            f"{', '.join(str(t) for t in missing)}."
        )
    through = manifest.get("bank_data_through")
    start = manifest.get("bank_data_from")
    if through and start:
        notes.append(
            f"Bank data was declared complete from {start} through {through}, "
            "for every account payments are made from, by whoever prepared the data."
        )
    elif through:
        notes.append(
            f"Bank data was declared complete through {through} by whoever "
            "prepared the data."
        )
    return (
        tuple(notes),
        manifest.get("fee_journals_recognised") is not False,
        start if isinstance(start, str) else None,
    )


def _fee_not_ruled_out(packet: EvidencePacket) -> EvidencePacket:
    """F14 means "no fee journal exists". Where no journal can be recognised as
    a fee (a Xero export types none as `bank_fee`), that absence proves nothing,
    so the shortfall is neither explained nor unexplained. Evidence, calculations
    and limitations stay; the finding and recommendations are not established."""
    diagnosis = Diagnosis(
        Disposition.ABSTAINED,
        None,
        reason="fee_journals_not_recognised",
        missing_information=(
            "journals recognisable as bank fees, so a fee can be ruled out",
        ),
    )
    return replace(packet, diagnosis=diagnosis, recommendations=())


def _before_bank_coverage(packet: EvidencePacket, bank_from: str) -> EvidencePacket:
    """F12 and pending settlement rest on finding no bank row. A payment dated
    before the bank data begins could have settled where nothing was supplied, so
    the absence proves nothing. Evidence and calculations stay; the finding and
    recommendations are not established."""
    early = any(
        item.record is not None
        and item.record.table == "payments"
        and dict(item.timestamps).get("payment_date", bank_from)[:10] < bank_from
        for item in packet.evidence
    )
    if not early:
        return packet
    diagnosis = Diagnosis(
        Disposition.ABSTAINED,
        None,
        reason="payment_before_bank_coverage",
        missing_information=("bank statements covering the payment date",),
    )
    return replace(packet, diagnosis=diagnosis, recommendations=())


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
        notes, fees_recognised, bank_from = _check_snapshot(snapshot_path, request)
        packet = _investigate(snapshot_path, request, limits, prov)
        if not fees_recognised and packet.diagnosis.category == "F14":
            packet = _fee_not_ruled_out(packet)
        if bank_from and packet.diagnosis.category in (F12, PENDING):
            packet = _before_bank_coverage(packet, bank_from)
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
