"""Plain-language paragraphs for a finished EvidencePacket.

Every sentence is a fixed template filled with values taken from the packet.
Nothing is generated and no model is called. The templates avoid four known
errors of model-written explanations: calling an all-clear a general clean bill,
adding actions the packet does not recommend, guessing which side of a
difference is wrong, and stating numbers or dates the packet does not hold.

A code (category, reason, action) without a template gets a generic sentence
that names the code and claims no meaning for it.
"""

from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    FindingState,
    Recommendation,
)

# Category -> what the conclusion says. Only observable conditions are claimed.
CATEGORIES: dict[str, str] = {
    "F06": (
        "This report concludes a possible duplicate payment (code F06): payments "
        "that have a posted bank debit are allocated to this invoice for more "
        "than the invoice total."
    ),
    "F12": (
        "This report concludes that this payment may be missing from the bank "
        "(code F12): the searches shown found no principal (non-fee) bank row "
        "for it, either by its reference or as an unmatched debit of the same "
        "amount, currency and counterparty account, and it is past its "
        "clearing limit."
    ),
    "F14": (
        "This report concludes an unexplained shortfall (code F14): the bank "
        "debit is smaller than the payment amount and no fee journal for the "
        "bank row was found. The cause is not known."
    ),
    "legitimate_split_payment": (
        "The records are consistent with a split payment: several "
        "payments whose allocations together equal the invoice total within "
        "the stated tolerance."
    ),
    "legitimate_due_date_payment": (
        "The records are consistent with a payment made on the "
        "invoice due date, with allocations equal to the invoice total within "
        "the stated tolerance."
    ),
    "legitimate_pending_settlement": (
        "The records are consistent with a payment that has no principal "
        "(non-fee) bank row in the searches shown yet but is still within its "
        "clearing limit."
    ),
    "legitimate_accounted_bank_fee": (
        "The records are consistent with a bank fee row that is "
        "tied to one payment and booked in the ledger."
    ),
    "legitimate_net_fee_accounting": (
        "The records are consistent with a bank debit smaller than "
        "the payment, where a bank fee journal accounts for the difference."
    ),
    "legitimate_weekend_clearing": (
        "The records are consistent with an ACH debit that cleared "
        "across a weekend within the clearing limit."
    ),
    "legitimate_check_clearing": (
        "The records are consistent with a check that cleared "
        "within the check clearing limit."
    ),
    "legitimate_batch_payment": (
        "The records are consistent with one payment allocated to several invoices."
    ),
    "legitimate_payment_reversal": (
        "The records are consistent with a payment that was "
        "reversed, where the bank debit, bank credit and reversal journal "
        "agree with the payment amount."
    ),
    "no_exception": (
        "No exception was found by the checks this tool has. This is not a "
        "general clean bill."
    ),
}

# Category -> short plain headline. It may not claim more than CATEGORIES does;
# an all-clear title says "consistent with" or "found by these checks".
TITLES: dict[str, str] = {
    "F06": "Possible duplicate payment",
    "F12": "Payment possibly missing from the bank",
    "F14": "Bank debit smaller than the payment, unexplained",
    "legitimate_split_payment": "Consistent with a split payment",
    "legitimate_due_date_payment": "Consistent with a payment made on the due date",
    "legitimate_pending_settlement": (
        "Consistent with a payment still within its clearing limit"
    ),
    "legitimate_accounted_bank_fee": (
        "Consistent with a bank fee that is booked in the ledger"
    ),
    "legitimate_net_fee_accounting": (
        "Consistent with a bank debit reduced by a booked bank fee"
    ),
    "legitimate_weekend_clearing": "Consistent with a payment clearing over a weekend",
    "legitimate_check_clearing": "Consistent with a check clearing within its limit",
    "legitimate_batch_payment": (
        "Consistent with one payment allocated to several invoices"
    ),
    "legitimate_payment_reversal": "Consistent with a reversed payment",
    "no_exception": "No exception found by these checks",
}

DISPOSITION_TITLES: dict[Disposition, str] = {
    Disposition.ABSTAINED: "No conclusion reached",
    Disposition.OUT_OF_SCOPE: "Not assessed",
    Disposition.FAILED: "Investigation did not complete",
}


def title(packet: EvidencePacket) -> str:
    d = packet.diagnosis
    if d.disposition is Disposition.ANSWERED:
        category = d.category or ""
        return TITLES.get(category, f"Conclusion with unrecognised code {category}")
    return DISPOSITION_TITLES[d.disposition]


# No search for an explanation of a difference runs: the check compares and
# stops, so the wording must not say that one was looked for and not found.
_NOT_EXPLAINED = "This tool does not establish an explanation for that difference."

# Reason -> why there is no conclusion. Used for abstained, out_of_scope and
# failed; the disposition decides the lead-in sentence.
REASONS: dict[str, str] = {
    # evidence missing or ambiguous
    "primary_entity_not_in_snapshot": (
        "The record is not in the data as of the cutoff."
    ),
    "required_records_missing": (
        "The records or relationships the check needs could not be established "
        "from the data. A record may be absent, or the records found may not "
        "form the single, valid set the check requires."
    ),
    "required_field_missing": ("A value the check needs is absent from the data."),
    "counterparty_unavailable": (
        "The data has no counterparty account for this payment or for a bank "
        "debit that could settle it, so the search for an unmatched debit could "
        "not run. No conclusion was drawn about whether the bank row is missing."
    ),
    "ambiguous_reference": (
        "The reference could not be tied to a single payment or a single bank "
        "debit, so the match is ambiguous."
    ),
    "fee_reference_unresolved": (
        "The payment reference of the bank fee row does not resolve to a payment."
    ),
    "unsupported_fx": (
        "The records involve more than one currency and this tool has no "
        "documented conversion path."
    ),
    "cutoff_differs_from_snapshot": (
        "The request's cutoff differs from the cutoff the data was prepared "
        "with, so the investigation did not run. Prepare the data again with "
        "this cutoff."
    ),
    "payment_before_bank_coverage": (
        "The payment is dated before the bank data starts, so a missing bank "
        "row says nothing. No conclusion was drawn."
    ),
    "unknown_payment_method": ("The payment method has no declared clearing limit."),
    "retrieval_row_limit": (
        "Record retrieval stopped at its row limit. The evidence is "
        "incomplete, not absent."
    ),
    "retrieval_time_limit": (
        "Record retrieval stopped at its time limit. The evidence is "
        "incomplete, not absent."
    ),
    "batch_search_limit_exceeded": (
        "There are too many unbanked payments to the same counterparty account "
        "to test for a batch debit, so that check was not run."
    ),
    # bank side
    "bank_row_not_a_posted_debit": (
        "The bank rows found for this payment include no posted debit."
    ),
    "bank_amount_above_payment": (
        "The bank debit is larger than the payment amount. " + _NOT_EXPLAINED
    ),
    "bank_row_before_payment": (
        "A bank debit is dated before the payment date. " + _NOT_EXPLAINED
    ),
    "clearing_lag_exceeds_limit": (
        "The bank debit posted later than the clearing limit for this payment "
        "method. " + _NOT_EXPLAINED
    ),
    "pending_payment_beyond_clearing_limit": (
        "The recorded status of the payment is pending or not final, and it is "
        "past its clearing limit. Its current status needs confirmation."
    ),
    "reference_unmatched_candidate_bank_row": (
        "A bank row whose payment reference matches no payment has the same "
        "amount, currency and counterparty account as the payment. It could not "
        "be confirmed as the settlement of this payment."
    ),
    "reference_unmatched_nearby_debit": (
        "A bank debit whose payment reference matches no payment, from the same "
        "counterparty account and currency, lies within the clearing window of "
        "this payment but does not equal its amount. It also equals no total "
        "of this payment together with some of the other unbanked payments to "
        "that account, among the combinations this tool tests. It was not "
        "confirmed as the settlement of this payment."
    ),
    "bank_batch_grouping_unmodelled": (
        "A bank debit whose payment reference matches no payment equals the "
        "total of this payment and some other unbanked payments to the same "
        "counterparty account. This tool cannot read how the bank grouped them, "
        "so it did not decide."
    ),
    "debit_differs_from_payment": (
        "A bank debit differs from the payment amount. " + _NOT_EXPLAINED
    ),
    # allocations and invoice
    "payment_without_invoice_allocation": (
        "The payment has no allocation to an invoice in its currency."
    ),
    "allocations_differ_from_payment": (
        "The invoice allocations of the payment do not add up to the payment "
        "amount. " + _NOT_EXPLAINED
    ),
    "invoice_over_allocated": (
        "The calculated open balance of an invoice is negative, because the "
        "allocations to it exceed its total. The case was not cleared."
    ),
    "commitment_not_proven_at_cutoff": (
        "The allocations to the invoice exceed its total, but a posted bank "
        "debit could not be confirmed for each over-allocating payment as of "
        "the cutoff. A duplicate payment was not concluded."
    ),
    # fee and reversal
    "accounted_fee_journal_missing": (
        "No bank fee journal was found for the bank fee row, so the fee could "
        "not be shown to be booked."
    ),
    "fee_journals_not_recognised": (
        "The bank debit is smaller than the payment, but this data was not "
        "declared to mark bank-fee journals, so a fee could explain the "
        "difference. No conclusion was drawn about the shortfall."
    ),
    "fee_journal_invalid": (
        "A bank fee journal exists, but it could not be established as balanced "
        "in one currency, with the lines attributed to this bank row also "
        "balanced and positive, so it was not used."
    ),
    "fee_journal_amount_differs": (
        "The bank fee journal amount differs from the bank fee row amount. "
        + _NOT_EXPLAINED
    ),
    "fee_journal_does_not_match_difference": (
        "A bank fee journal exists but does not equal the difference between "
        "the payment and the bank debit."
    ),
    "reversal_amounts_differ": (
        "The reversal debit, credit and journal amounts do not agree with "
        "each other and the payment."
    ),
    "reversal_credit_before_debit": (
        "The reversal credit posted before the debit. " + _NOT_EXPLAINED
    ),
    "reversal_followed_by_repayment": (
        "The payment was reversed, but another payment also pays the invoice. "
        "This needs a person's input."
    ),
    "reversal_invoice_still_paid": (
        "The payment was reversed, but the invoice still shows as paid. This "
        "needs a person's input."
    ),
    # approval and journal checks that run last on an all-clear
    "invoice_approval_not_completed": (
        "No completed final approval was found for an invoice the payment "
        "settles, as of the payment date."
    ),
    "invoice_approval_not_effective": (
        "An invoice was approved once, but its newest approval event is not a "
        "final approval, so approval was not in effect on the payment date."
    ),
    "payment_before_approval": (
        "The final approval of an invoice is recorded after the payment date."
    ),
    "settlement_contradicted_by_bank": (
        "A payment counted toward this invoice also has a bank reversal, credit "
        "or return, so whether it was finally paid is contradictory. No "
        "conclusion was drawn."
    ),
    "payment_over_allocated": (
        "The allocations of the payment exceed the payment amount. " + _NOT_EXPLAINED
    ),
    "payment_journal_missing": (
        "No balanced payment journal sourced to the payment was found."
    ),
    "payment_journal_unbalanced": (
        "A payment journal could not be established as balanced in one "
        "currency, with the lines attributed to this payment also balanced and "
        "positive."
    ),
    "payment_journal_currency_differs": (
        "A payment journal is in a different currency than the payment."
    ),
    "payment_journal_amount_differs": (
        "The payment journal amount differs from the payment amount. " + _NOT_EXPLAINED
    ),
    # out of scope
    "entity_type_not_supported_yet": (
        "This tool examines invoices, payments and bank rows only. This kind "
        "of record was not examined."
    ),
    "bank_row_type_not_supported_yet": (
        "Of the bank rows, this tool examines bank fee rows only. This bank "
        "row was not examined."
    ),
    "no_in_scope_pattern": (
        "None of the situations this tool checks applies to this record."
    ),
    # failed
    "snapshot_manifest_unreadable": (
        "The manifest that goes with the data could not be read."
    ),
    "snapshot_mismatch": (
        "The data file does not match the snapshot named in the request."
    ),
    "snapshot_unreadable": "The data file could not be read.",
    "malformed_record": (
        "A record in the data was malformed or not unique, or the result was "
        "internally inconsistent."
    ),
    "unexpected_exception": (
        "An unexpected error stopped the investigation of this case."
    ),
}


# The contract defines a difference as the first amount minus all the others.
OPERATION_WORDS = {
    "sum": "sum of the amounts",
    "difference": "first amount minus the others",
    "abs_difference": "absolute difference of two amounts",
}


def _reason_sentence(reason: str) -> str:
    known = REASONS.get(reason)
    if known is not None:
        return known
    return f"The reason code is '{reason}'. This report has no wording for it."


def _category_sentence(category: str) -> str:
    known = CATEGORIES.get(category)
    if known is not None:
        return known
    return f"The category is '{category}'. This report has no wording for it."


def _list(items: tuple[str, ...]) -> str:
    return "; ".join(items)


def _recommendation(r: Recommendation) -> str:
    text = f"Proposed next step: {r.action_type}."
    if r.prerequisites:
        text += f" Before acting: {_list(r.prerequisites)}"
    if r.unresolved_assumptions:
        text += f" Assumptions not yet resolved: {_list(r.unresolved_assumptions)}"
    return text


def _outcome(packet: EvidencePacket) -> list[str]:
    d = packet.diagnosis
    reason = d.reason or ""
    match d.disposition:
        case Disposition.ANSWERED:
            category = d.category or ""
            out = [_category_sentence(category)]
            if category.startswith("legitimate_") or category == "no_exception":
                out.append(
                    "This covers only the checks named in the scope statement "
                    "below. It is not a statement that nothing else is wrong."
                )
            return out
        case Disposition.ABSTAINED:
            return [
                "No conclusion was reached. This is not an all-clear.",
                _reason_sentence(reason),
            ]
        case Disposition.OUT_OF_SCOPE:
            return [
                "This tool did not assess this record. That is not a finding "
                "that the record is correct.",
                _reason_sentence(reason),
            ]
        case Disposition.FAILED:
            return [
                "The investigation did not complete. There is no conclusion "
                "and this is not an all-clear.",
                _reason_sentence(reason),
            ]
    raise AssertionError(d.disposition)  # pragma: no cover


def summary(packet: EvidencePacket) -> tuple[str, ...]:
    """The point of the report: record, outcome, reason, findings, what is
    missing and every limitation."""
    d = packet.diagnosis
    req = packet.request
    paras = [
        f"This report covers {req.primary_entity.table} record "
        f"{req.primary_entity.record_id}, using data up to the cutoff "
        f"{req.cutoff.isoformat()}.",
        *_outcome(packet),
    ]
    if d.reason is not None and d.disposition is not Disposition.ANSWERED:
        paras.append(f"Reason code: {d.reason}.")

    for state, label in (
        (FindingState.SUPPORTED, "Findings the records support"),
        (FindingState.CONTRADICTED, "Findings the records contradict"),
        (FindingState.UNKNOWN, "Findings that could not be decided"),
    ):
        ids = tuple(f.check_id for f in d.findings if f.state is state)
        if ids:
            paras.append(f"{label}: {_list(ids)}.")

    if d.missing_information:
        paras.append(f"Still missing: {_list(d.missing_information)}.")

    paras.extend(f"Limitation: {text}" for text in packet.limitations)
    return tuple(paras)


def explain(packet: EvidencePacket) -> tuple[str, ...]:
    """The summary plus the figures and next steps, for plain-text and JSON use."""
    paras = list(summary(packet))
    for c in packet.calculations:
        how = OPERATION_WORDS.get(c.operation, c.operation)
        paras.append(f"Calculation {c.calculation_id} ({how}): {c.result}.")
    for dc in packet.day_counts:
        paras.append(
            f"Day count {dc.calculation_id}: {dc.result} business days, "
            f"compared with the limit of {dc.limit} ({dc.limit_policy})."
        )
    for r in packet.recommendations:
        paras.append(_recommendation(r))
    if not packet.recommendations:
        paras.append("No next step is proposed.")
    return tuple(paras)
