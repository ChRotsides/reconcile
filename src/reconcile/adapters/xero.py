"""Xero Accounting API objects saved as JSON, read into contract rows.

Input is files a user saved from the Invoices, Payments, BatchPayments and
Journals endpoints (no live API call is made). The mapping, its citations and
the interpretations it makes are in docs/input-mapping.md sections 3 and 10.

Numbers are parsed from the JSON text into `Decimal`, never through a float.
Anything the mapping cannot represent stops the read with the file and the
object ID, several problems at a time up to PROBLEM_CAP; nothing is dropped
silently. Objects outside the contract's scope (receivables, credit-note
payments) are skipped and counted in `extras`.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from reconcile.adapters.inputs import Problems
from reconcile.diagnosis.policy import CLEARING_LIMITS

KINDS = ("Invoices", "Payments", "BatchPayments", "Journals")
REFERENCE_FIELDS = ("Reference", "Details")
_ID_KEY = {
    "Invoices": "InvoiceID",
    "Payments": "PaymentID",
    "BatchPayments": "BatchPaymentID",
    "Journals": "JournalID",
}
_MS_DATE = re.compile(r"/Date\((-?[0-9]+)\+0000\)/")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Payment.Status has only these two values (docs/input-mapping.md 3.2). AUTHORISED
# means "the ledger records this payment as made", an interpretation (section 10).
_STATUS = {
    "AUTHORISED": ("completed", "settled"),
    "DELETED": ("reversed", "reversed"),
}

Rows = list[dict[str, str | None]]


@dataclass
class XeroData:
    tables: dict[str, Rows] = field(default_factory=dict)
    # table -> one description ("Payments.json PaymentID abc") per row, same order
    where: dict[str, list[str]] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


class _Bad(Exception):
    """A value that cannot be mapped; the caller attaches the object's name."""


def xero_date(value: object) -> str:
    """ISO date text for a Xero date value. Accepts '/Date(ms+0000)/' at UTC
    midnight and 'YYYY-MM-DD'; refuses anything else, including other offsets
    (the spec does not say what an offset means) and times of day."""
    if not isinstance(value, str):
        raise _Bad(f"date must be text, not {type(value).__name__}")
    m = _MS_DATE.fullmatch(value)
    if m:
        try:
            moment = _EPOCH + timedelta(milliseconds=int(m.group(1)))
        except (OverflowError, ValueError):  # beyond the years a date can hold
            raise _Bad(f"date {value!r} is out of range") from None
        if (moment.hour, moment.minute, moment.second, moment.microsecond) != (
            0,
            0,
            0,
            0,
        ):
            raise _Bad(f"date {value!r} is not at midnight UTC")
        return moment.date().isoformat()
    if _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise _Bad(f"date {value!r} is neither /Date(ms+0000)/ nor YYYY-MM-DD")


def _amount(value: object, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | str):
        raise _Bad(f"{what} must be a number, not {type(value).__name__}")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise _Bad(f"{what} {value!r} is not a number") from None
    if not d.is_finite():
        raise _Bad(f"{what} {value!r} is not finite")
    return d


def _text(d: Decimal) -> str:
    return format(d, "f")


def _str(obj: Mapping[str, Any], key: str, what: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _Bad(f"{what} is missing")
    return v.strip()


def _optional_str(obj: Mapping[str, Any], key: str) -> str | None:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise _Bad(f"{key} must be text")
    return v.strip() or None


def _sub(obj: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    v = obj.get(key)
    return v if isinstance(v, dict) else {}


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _load(
    kind: str, paths: Sequence[Path], problems: Problems
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Objects of one kind as (file name, object), exact repeats across files
    removed (the count is returned) and conflicting repeats reported."""
    key = _ID_KEY[kind]
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    repeats = 0
    for path in paths:
        try:
            doc = json.loads(
                path.read_text(encoding="utf-8"),
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=_refuse_constant,
            )
        except (OSError, UnicodeDecodeError, ValueError) as e:
            problems.add(f"cannot read as JSON: {e}", path.name)
            continue
        items = doc.get(kind) if isinstance(doc, dict) else None
        if not isinstance(items, list):
            problems.add(f"expected an object with a {kind!r} list", path.name)
            continue
        for i, obj in enumerate(items):
            if not isinstance(obj, dict) or not isinstance(obj.get(key), str):
                problems.add(f"no {key}", f"{path.name} {kind}[{i}]")
                continue
            ident = obj[key]
            if ident in found:
                first_file, first = found[ident]
                if first == obj:
                    repeats += 1
                else:
                    problems.add(
                        f"differs from the same {key} in {first_file}",
                        f"{path.name} {kind} {ident}",
                    )
                continue
            found[ident] = (path.name, obj)
            out.append((path.name, obj))
    return out, repeats


def _method(
    account: Mapping[str, Any],
    by_account: Mapping[str, str],
    default: str | None,
) -> str | None:
    code = account.get("Code")
    if isinstance(code, str) and code in by_account:
        return by_account[code]
    return default


def check_methods(by_account: Mapping[str, str], default: str | None) -> None:
    """Refuse a payment method the investigator would not recognise."""
    values = [*by_account.values(), *([default] if default else [])]
    bad = sorted({v for v in values if v not in CLEARING_LIMITS})
    if bad:
        raise ValueError(f"payment methods {bad} are not among {list(CLEARING_LIMITS)}")


def _agree(
    standalone: tuple[str, dict[str, Any]],
    bid: str,
    status: str,
    invoice_id: str,
    amount: Decimal,
) -> None:
    """The member's own record in Payments must say the same as the batch."""
    where, pay = standalone
    named = pay.get("BatchPaymentID")
    if named is None:
        raise _Bad(f"{where} is listed by batch {bid} but has no BatchPaymentID")
    if str(named) != bid:
        raise _Bad(f"{where} names batch {named}, but batch {bid} lists it")
    if pay.get("Status") != status:
        raise _Bad(
            f"{where}: Status {pay.get('Status')!r} differs from the batch's {status!r}"
        )
    own_invoice = _sub(pay, "Invoice").get("InvoiceID")
    if own_invoice != invoice_id:
        raise _Bad(
            f"{where}: invoice {own_invoice} differs from invoice {invoice_id} "
            f"in batch {bid}"
        )
    own_amount = _amount(pay.get("Amount"), "Amount")
    if own_amount != amount:
        raise _Bad(f"{where}: Amount {own_amount} differs from {amount} in batch {bid}")


def read_xero(
    files: Mapping[str, Sequence[Path]],
    *,
    base_currency: str,
    reference_field: str = "Reference",
    method_by_account: Mapping[str, str] | None = None,
    default_method: str | None = None,
) -> XeroData:
    """Contract rows for invoices, payments, payment_allocations and gl_entries.

    `files` maps each of KINDS to the saved JSON files holding it (an empty
    list is a refusal: an endpoint that was not saved is not an empty one).
    """
    if reference_field not in REFERENCE_FIELDS:
        raise ValueError(f"reference_field must be one of {list(REFERENCE_FIELDS)}")
    by_account = dict(method_by_account or {})
    check_methods(by_account, default_method)
    for kind in KINDS:
        if not files.get(kind):
            raise ValueError(
                f"no {kind} file given; save the {kind} response even when it is "
                "empty, so a missing export is not read as no records"
            )
    problems = Problems("Xero files", "object")
    loaded = {k: _load(k, files[k], problems) for k in KINDS}

    def reference(obj: Mapping[str, Any]) -> str | None:
        return _optional_str(obj, reference_field)

    out = XeroData(
        tables={
            "invoices": [],
            "payments": [],
            "payment_allocations": [],
            "gl_entries": [],
        },
        where={
            "invoices": [],
            "payments": [],
            "payment_allocations": [],
            "gl_entries": [],
        },
    )

    def add(table: str, where: str, row: dict[str, str | None]) -> None:
        out.tables[table].append(row)
        out.where[table].append(where)

    # --- invoices (ACCPAY only)
    currency_of: dict[str, str] = {}
    other_invoices: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    with_credit = 0
    for fname, inv in loaded["Invoices"][0]:
        iid = inv["InvoiceID"]
        where = f"{fname} Invoices {iid}"
        try:
            itype = inv.get("Type")
            if itype != "ACCPAY":
                other_invoices[str(itype)] += 1
                continue
            currency = _str(inv, "CurrencyCode", "CurrencyCode")
            total = _amount(inv.get("Total"), "Total")
            due = inv.get("DueDate")
            status = _str(inv, "Status", "Status")
            credited = inv.get("AmountCredited")
            if credited is not None and _amount(credited, "AmountCredited") != 0:
                with_credit += 1
            status_counts[status] += 1
            currency_of[iid] = currency
            add(
                "invoices",
                where,
                {
                    "invoice_id": iid,
                    "due_date": xero_date(due) if due is not None else None,
                    "currency": currency,
                    "invoice_total": _text(total),
                    "status": "paid" if status == "PAID" else status.lower(),
                },
            )
        except _Bad as e:
            problems.add(str(e), where)

    # --- payments
    # A payment that a batch lists, or that names a batch, is represented by the
    # batch alone; the two records must agree, or it would be counted twice or
    # with the batch's version of the money.
    listed: dict[str, list[str]] = {}
    for _fname, batch in loaded["BatchPayments"][0]:
        if batch.get("Type") == "PAYBATCH" and isinstance(batch.get("Payments"), list):
            for member in batch["Payments"]:
                if isinstance(member, dict) and isinstance(
                    member.get("PaymentID"), str
                ):
                    listed.setdefault(member["PaymentID"], []).append(
                        str(batch["BatchPaymentID"])
                    )
    other_payments: Counter[str] = Counter()
    claimed: dict[str, tuple[str, dict[str, Any]]] = {}  # standalone batch members
    for fname, pay in loaded["Payments"][0]:
        pid = pay["PaymentID"]
        where = f"{fname} Payments {pid}"
        try:
            ptype = pay.get("PaymentType")
            if ptype != "ACCPAYPAYMENT":
                other_payments[str(ptype)] += 1
                continue
            invoice_id = _str(_sub(pay, "Invoice"), "InvoiceID", "Invoice.InvoiceID")
            if invoice_id not in currency_of:
                raise _Bad(
                    f"invoice {invoice_id} is not an ACCPAY invoice in the "
                    "Invoices files"
                )
            if pay.get("BatchPaymentID") is not None or pid in listed:
                claimed[pid] = (where, pay)
                continue
            amount = _amount(pay.get("Amount"), "Amount")
            if amount < 0:
                raise _Bad("Amount is negative")
            status = _str(pay, "Status", "Status")
            if status not in _STATUS:
                raise _Bad(f"Status {status!r} is not one of {sorted(_STATUS)}")
            pay_status, settle = _STATUS[status]
            account = _sub(pay, "Account")
            add(
                "payments",
                where,
                {
                    "payment_id": pid,
                    "bank_account_id": _optional_str(pay, "BankAccountNumber"),
                    "reference_number": reference(pay),
                    "payment_date": xero_date(pay.get("Date")),
                    "payment_method": _method(account, by_account, default_method),
                    "payment_currency": currency_of[invoice_id],
                    "payment_amount": _text(amount),
                    "payment_status": pay_status,
                    "settlement_status": settle,
                },
            )
            add(
                "payment_allocations",
                where,
                {
                    "payment_id": pid,
                    "invoice_id": invoice_id,
                    "allocated_amount": _text(amount),
                    "allocation_date": xero_date(pay.get("Date")),
                },
            )
        except _Bad as e:
            problems.add(str(e), where)

    # --- batch payments: one contract payment, member payments as allocations
    other_batches: Counter[str] = Counter()
    batch_members: dict[str, list[str]] = {}
    seen_batches: set[str] = set()
    for fname, batch in loaded["BatchPayments"][0]:
        bid = batch["BatchPaymentID"]
        where = f"{fname} BatchPayments {bid}"
        try:
            btype = batch.get("Type")
            if btype != "PAYBATCH":
                other_batches[str(btype)] += 1
                continue
            seen_batches.add(bid)
            status = _str(batch, "Status", "Status")
            if status not in _STATUS:
                raise _Bad(f"Status {status!r} is not one of {sorted(_STATUS)}")
            pay_status, settle = _STATUS[status]
            listed_members = batch.get("Payments")
            if not isinstance(listed_members, list) or not listed_members:
                raise _Bad("has no member Payments")
            date_text = xero_date(batch.get("Date"))
            per_invoice: dict[str, Decimal] = {}
            currencies: set[str] = set()
            ids: list[str] = []
            total = Decimal(0)
            for k, member in enumerate(listed_members):
                if not isinstance(member, dict):
                    raise _Bad(f"Payments[{k}] is not an object")
                mid = _str(member, "PaymentID", f"Payments[{k}].PaymentID")
                ids.append(mid)
                times = listed.get(mid, [])
                if len(times) > 1:
                    raise _Bad(
                        f"member {mid} is listed {len(times)} times, by "
                        f"batches {sorted(set(times))}; a payment belongs to "
                        "one batch, once"
                    )
                invoice_id = _str(
                    _sub(member, "Invoice"), "InvoiceID", f"Payments[{k}].Invoice"
                )
                if invoice_id not in currency_of:
                    raise _Bad(
                        f"member {mid}: invoice {invoice_id} is not an ACCPAY "
                        "invoice in the Invoices files"
                    )
                amount = _amount(member.get("Amount"), f"Payments[{k}].Amount")
                if amount < 0:
                    raise _Bad(f"member {mid}: Amount is negative")
                if mid in claimed:
                    _agree(claimed[mid], bid, status, invoice_id, amount)
                per_invoice[invoice_id] = (
                    per_invoice.get(invoice_id, Decimal(0)) + amount
                )
                currencies.add(currency_of[invoice_id])
                total += amount
            if len(currencies) != 1:
                raise _Bad(
                    f"member invoices have several currencies {sorted(currencies)}"
                )
            declared = _amount(batch.get("TotalAmount"), "TotalAmount")
            if declared != total:
                raise _Bad(
                    f"TotalAmount {declared} differs from the sum of its member "
                    f"Payments {total}"
                )
            batch_members[bid] = ids
            add(
                "payments",
                where,
                {
                    "payment_id": bid,
                    "bank_account_id": None,  # members may pay different suppliers
                    "reference_number": reference(batch),
                    "payment_date": date_text,
                    "payment_method": _method(
                        _sub(batch, "Account"), by_account, default_method
                    ),
                    "payment_currency": next(iter(currencies)),
                    "payment_amount": _text(total),
                    "payment_status": pay_status,
                    "settlement_status": settle,
                },
            )
            for invoice_id, amount in per_invoice.items():
                add(
                    "payment_allocations",
                    where,
                    {
                        "payment_id": bid,
                        "invoice_id": invoice_id,
                        "allocated_amount": _text(amount),
                        "allocation_date": date_text,
                    },
                )
        except _Bad as e:
            problems.add(str(e), where)
    for pid, (twhere, tpay) in claimed.items():
        named = tpay.get("BatchPaymentID")
        if named is None:
            continue  # listed by a batch; _agree reported the missing BatchPaymentID
        if str(named) not in seen_batches:
            problems.add(
                f"names batch {named}, which is not a PAYBATCH in the "
                "BatchPayments files",
                twhere,
            )
        elif str(named) not in listed.get(pid, []):
            problems.add("names this batch but is not among its Payments", twhere)

    # --- journals
    other_sources: Counter[str] = Counter()
    unbalanced: list[str] = []
    no_lines: list[str] = []
    for fname, jr in loaded["Journals"][0]:
        jid = jr["JournalID"]
        where = f"{fname} Journals {jid}"
        try:
            source_type = _str(jr, "SourceType", "SourceType")
            source_id = _str(jr, "SourceID", "SourceID")
            posting = xero_date(jr.get("JournalDate"))
            lines = jr.get("JournalLines")
            if not isinstance(lines, list) or not lines:
                no_lines.append(jid)
                continue
            if source_type == "ACCPAYPAYMENT":
                kind = "payment"
            else:
                kind = f"xero_{source_type.lower()}"
                other_sources[source_type] += 1
            net = Decimal(0)
            for k, line in enumerate(lines):
                if not isinstance(line, dict):
                    raise _Bad(f"JournalLines[{k}] is not an object")
                lid = _str(line, "JournalLineID", f"JournalLines[{k}].JournalLineID")
                amount = _amount(line.get("NetAmount"), f"JournalLines[{k}].NetAmount")
                net += amount
                add(
                    "gl_entries",
                    f"{where} line {lid}",
                    {
                        "journal_id": jid,
                        "journal_line_id": lid,
                        "source_transaction_id": source_id,
                        "transaction_type": kind,
                        "posting_date": posting,
                        "gl_account": _optional_str(line, "AccountCode"),
                        "debit": _text(amount) if amount > 0 else "0",
                        "credit": _text(-amount) if amount < 0 else "0",
                        "currency": base_currency,
                    },
                )
            if net != 0:
                unbalanced.append(jid)
        except _Bad as e:
            problems.add(str(e), where)

    problems.raise_if_any()
    out.extras = {
        "objects_read": {k: len(loaded[k][0]) for k in KINDS},
        "exact_repeats_across_files": {k: loaded[k][1] for k in KINDS},
        "invoices_other_types_skipped": dict(sorted(other_invoices.items())),
        "invoice_statuses": dict(sorted(status_counts.items())),
        "invoices_with_nonzero_AmountCredited": with_credit,
        "payments_other_types_skipped": dict(sorted(other_payments.items())),
        "batches_other_types_skipped": dict(sorted(other_batches.items())),
        "batch_member_payment_ids": batch_members,
        "journals_other_source_types": dict(sorted(other_sources.items())),
        "journals_not_summing_to_zero": unbalanced,
        "journals_without_lines": no_lines,
        "reference_field": reference_field,
        "base_currency_for_journals": base_currency,
    }
    return out
