"""Retrieval for payment- and bank-row-primary investigations (evidence-contract
sections 4.2, 4.3 and 5). Every query is bounded by the Reader's limits and
observes the cutoff; lookups are by token only, never by amount or date alone
(the one exception is the contract's A3 candidate search, which also fixes
currency and counterparty)."""

from datetime import date

from reconcile.retrieval.snapshot import Reader, Row

PAYMENT_COLS = (
    "payment_id, vendor_id, bank_account_id, reference_number, payment_date,"
    " payment_method, payment_currency, payment_amount, payment_status,"
    " settlement_status"
)
BANK_COLS = (
    "bank_transaction_id, counterparty_token, payment_reference, posted_date,"
    " transaction_type, amount, currency, direction, status"
)


def payment(reader: Reader, token: str, cutoff: date) -> Row | None:
    got = reader.rows(
        f"SELECT {PAYMENT_COLS} FROM payments WHERE payment_id = ?"
        " AND payment_date <= ?",
        (token, cutoff.isoformat()),
    )
    if len(got) > 1:
        raise ValueError("payment is not unique in the snapshot")
    return got[0] if got else None


def payments_with_reference(reader: Reader, reference: str) -> list[Row]:
    return reader.rows(
        f"SELECT {PAYMENT_COLS} FROM payments WHERE reference_number = ?"
        " ORDER BY payment_id",
        (reference,),
    )


def principal_rows(reader: Reader, reference: str, cutoff: date) -> list[Row]:
    """Observable non-fee bank rows carrying the reference (section 3)."""
    return reader.rows(
        f"SELECT {BANK_COLS} FROM bank_transactions WHERE payment_reference = ?"
        " AND transaction_type <> 'bank fee' AND posted_date <= ?"
        " ORDER BY bank_transaction_id",
        (reference, cutoff.isoformat()),
    )


def bank_row(reader: Reader, token: str, cutoff: date) -> Row | None:
    got = reader.rows(
        f"SELECT {BANK_COLS} FROM bank_transactions WHERE bank_transaction_id = ?"
        " AND posted_date <= ?",
        (token, cutoff.isoformat()),
    )
    if len(got) > 1:
        raise ValueError("bank transaction is not unique in the snapshot")
    return got[0] if got else None


def allocations_of_payment(reader: Reader, token: str, cutoff: date) -> list[Row]:
    return reader.rows(
        "SELECT payment_id, invoice_id, allocated_amount, allocation_date"
        " FROM payment_allocations WHERE payment_id = ? AND allocation_date <= ?"
        " ORDER BY invoice_id",
        (token, cutoff.isoformat()),
    )


def journals(
    reader: Reader, source: str, gl_type: str, cutoff: date
) -> dict[str, list[Row]]:
    """Observable GL journals that have a line of one transaction type sourced to
    a record, by journal. Each journal is returned complete (every observable
    line of that journal_id, whatever its source or type), so that a balance
    claim covers the whole journal and not only the lines found by the filter."""
    lines = reader.rows(
        "SELECT journal_id, source_transaction_id, transaction_type, posting_date,"
        " debit, credit, currency FROM gl_entries"
        " WHERE posting_date <= ? AND journal_id IN (SELECT journal_id FROM gl_entries"
        " WHERE source_transaction_id = ? AND transaction_type = ?"
        " AND posting_date <= ?) ORDER BY journal_id, journal_line_id",
        (cutoff.isoformat(), source, gl_type, cutoff.isoformat()),
    )
    out: dict[str, list[Row]] = {}
    for line in lines:
        out.setdefault(line["journal_id"] or "", []).append(line)
    return out


def unreferenced_debits(
    reader: Reader, counterparty: str, currency: str, cutoff: date
) -> list[Row]:
    """Section 4.2 A3 pool: observable non-fee debits whose reference matches no
    payment, in one currency and from one counterparty (any amount)."""
    return reader.rows(
        f"SELECT {BANK_COLS} FROM bank_transactions b WHERE direction = 'debit'"
        " AND transaction_type <> 'bank fee' AND currency = ?"
        " AND counterparty_token = ? AND posted_date <= ?"
        " AND NOT EXISTS (SELECT 1 FROM payments p"
        " WHERE p.reference_number = b.payment_reference)"
        " ORDER BY bank_transaction_id",
        (currency, counterparty, cutoff.isoformat()),
    )


def debits_without_counterparty(
    reader: Reader, currency: str, cutoff: date
) -> list[Row]:
    """Rows the A3 pool would hold if their counterparty were known: same filters
    as `unreferenced_debits`, counterparty absent. One is enough to know the A3
    search cannot be trusted; the Reader's row limit bounds the rest."""
    return reader.rows(
        f"SELECT {BANK_COLS} FROM bank_transactions b WHERE direction = 'debit'"
        " AND transaction_type <> 'bank fee' AND currency = ?"
        " AND counterparty_token IS NULL AND posted_date <= ?"
        " AND NOT EXISTS (SELECT 1 FROM payments p"
        " WHERE p.reference_number = b.payment_reference)"
        " ORDER BY bank_transaction_id",
        (currency, cutoff.isoformat()),
    )


def unbanked_payments(
    reader: Reader, counterparty: str, currency: str, cutoff: date
) -> list[Row]:
    """Effective payments to one counterparty with no observable principal row."""
    return reader.rows(
        f"SELECT {PAYMENT_COLS} FROM payments p WHERE bank_account_id = ?"
        " AND payment_currency = ? AND payment_date <= ?"
        " AND payment_status = 'completed' AND settlement_status = 'settled'"
        " AND NOT EXISTS (SELECT 1 FROM bank_transactions b"
        " WHERE b.payment_reference = p.reference_number"
        " AND b.transaction_type <> 'bank fee' AND b.posted_date <= ?)"
        " ORDER BY payment_id",
        (counterparty, currency, cutoff.isoformat(), cutoff.isoformat()),
    )


def equal_twins(reader: Reader, pay: Row) -> list[Row]:
    """Other payments with the same vendor, amount, date and currency. They stay
    distinct payments (principle 4); this only makes the look-alike visible."""
    return reader.rows(
        f"SELECT {PAYMENT_COLS} FROM payments WHERE vendor_id = ?"
        " AND payment_amount = ? AND payment_date = ?"
        " AND payment_currency = ? AND payment_id <> ?"
        " ORDER BY payment_id",
        (
            pay["vendor_id"] or "",
            pay["payment_amount"] or "",
            pay["payment_date"] or "",
            pay["payment_currency"] or "",
            pay["payment_id"] or "",
        ),
    )
