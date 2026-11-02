"""Retrieval for an invoice-primary investigation (evidence-contract section 4.1).

Only the columns the contract names are selected. `invoice_date` and
`received_date` are deliberately never read: invoice age must not be diagnostic
evidence for F06 (docs/leakage-test.md residual R4).
"""

from dataclasses import dataclass
from datetime import date

from reconcile.retrieval.snapshot import Reader, Row


@dataclass(frozen=True)
class InvoiceFacts:
    invoice: Row
    allocations: tuple[Row, ...]  # allocated on or before the cutoff
    payments: dict[str, Row]  # payment token -> row, dated on or before the cutoff
    reference_counts: dict[str, int]  # reference token -> number of payments
    bank_rows: dict[
        str, tuple[Row, ...]
    ]  # reference token -> observable principal rows


def gather_invoice(reader: Reader, invoice: str, cutoff: date) -> InvoiceFacts:
    day = cutoff.isoformat()
    inv = reader.rows(
        "SELECT currency, invoice_total, due_date, status FROM invoices"
        " WHERE invoice_id = ?",
        (invoice,),
    )
    if len(inv) != 1:
        raise ValueError("invoice is not unique in the snapshot")
    allocations = reader.rows(
        "SELECT payment_id, allocated_amount, allocation_date"
        " FROM payment_allocations WHERE invoice_id = ? AND allocation_date <= ?"
        " ORDER BY payment_id",
        (invoice, day),
    )
    payments: dict[str, Row] = {}
    for a in allocations:
        pid = a["payment_id"]
        assert pid is not None
        found = reader.rows(
            "SELECT reference_number, payment_date, payment_method,"
            " payment_currency, payment_amount, payment_status, settlement_status"
            " FROM payments WHERE payment_id = ? AND payment_date <= ?",
            (pid, day),
        )
        if found:
            payments[pid] = found[0]
    counts: dict[str, int] = {}
    bank: dict[str, tuple[Row, ...]] = {}
    for p in payments.values():
        ref = p["reference_number"]
        if ref is None or ref in counts:
            continue
        n = reader.rows(
            "SELECT COUNT(*) AS n FROM payments WHERE reference_number = ?", (ref,)
        )
        counts[ref] = int(n[0]["n"] or 0)
        bank[ref] = tuple(
            reader.rows(
                "SELECT bank_transaction_id, posted_date, transaction_type, amount,"
                " currency, direction, status FROM bank_transactions"
                " WHERE payment_reference = ? AND transaction_type <> 'bank fee'"
                " AND posted_date <= ? ORDER BY bank_transaction_id",
                (ref, day),
            )
        )
    return InvoiceFacts(inv[0], tuple(allocations), payments, counts, bank)
