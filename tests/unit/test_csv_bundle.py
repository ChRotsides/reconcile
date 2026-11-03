"""CSV bundles: a public fixture is rewritten into a spreadsheet-style export
(renamed columns, other date formats, decimal commas, mapped statuses) and must
give the outcome expected.json gives for the original."""

import csv
import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.csv_bundle import prepare_csv_bundle
from reconcile.adapters.finrca import TABLES
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import InvestigationRequest, RecordRef

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "public"
EXAMPLE = ROOT / "examples" / "csv_bundle"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
CUTOFF = date(2026, 6, 30)
TABLES_USED = (
    "invoices",
    "payments",
    "payment_allocations",
    "bank_transactions",
    "gl_entries",
    "approval_events",
)
CLOSED = {
    ("payments", "payment_status"),
    ("payments", "settlement_status"),
    ("bank_transactions", "status"),
    ("bank_transactions", "direction"),
}


def _money_text(v: str) -> str:
    return f"{Decimal(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def write_bundle(
    name: str,
    folder: Path,
    through: str = "2026-07-31",
    start: str = "2026-01-01",
    matching: str = "normalised",
) -> None:
    """Rewrite fixture `name` as semicolon-separated exports with a BOM, CRLF
    line ends, a blank trailing line and renamed columns, plus its mapping."""
    folder.mkdir(parents=True, exist_ok=True)
    toml = [
        "version = 1\n"
        f'bank_data_from = "{start}"\nbank_data_through = "{through}"\n'
        "all_paying_accounts_covered = true\n"
        f'reference_matching = "{matching}"\nfee_journals_typed = true\n'
    ]
    for table in TABLES_USED:
        cols = {c.name: c for c in TABLES[table].columns}
        with (FIXTURES / name / f"{table}.csv").open(newline="") as f:
            source = list(csv.DictReader(f))
        toml.append(f'[tables.{table}]\nfile = "{table}_x.csv"\ndelimiter = ";"')
        toml.append(f"[tables.{table}.columns]")
        out_rows: list[dict[str, str]] = [{} for _ in source]
        for col, spec in cols.items():
            head = f"Src {col}"
            if spec.kind == "date":
                fmt = "%d/%m/%Y"
                toml.append(
                    f'{col} = {{ column = "{head}", date_formats = ["{fmt}"] }}'
                )
            elif spec.kind == "timestamp":
                fmt = "%d/%m/%Y %H:%M:%S"
                toml.append(
                    f'{col} = {{ column = "{head}", date_formats = ["{fmt}"] }}'
                )
            elif spec.kind in ("money", "money_of"):
                toml.append(
                    f'{col} = {{ column = "{head}", decimal_separator = ",", '
                    'thousands_separator = "." }'
                )
            elif (table, col) in CLOSED:
                vals = sorted({r[col] for r in source if r[col]})
                pairs = ", ".join(f'"<{v}>" = "{v}"' for v in vals)
                toml.append(f'{col} = {{ column = "{head}", values = {{ {pairs} }} }}')
            else:
                toml.append(f'{col} = "{head}"')
            for r, o in zip(source, out_rows, strict=True):
                v = r[col]
                if v and spec.kind == "date":
                    v = date.fromisoformat(v).strftime("%d/%m/%Y")
                elif v and spec.kind == "timestamp":
                    v = datetime.fromisoformat(v).strftime("%d/%m/%Y %H:%M:%S")
                elif v and spec.kind in ("money", "money_of"):
                    v = _money_text(v)
                elif v and (table, col) in CLOSED:
                    v = f"<{v}>"
                o[head] = v
        lines = [";".join(f"Src {c}" for c in cols)]
        lines += [";".join(o[f"Src {c}"] for c in cols) for o in out_rows]
        text = "\r\n".join(lines) + "\r\n\r\n"
        (folder / f"{table}_x.csv").write_bytes(b"\xef\xbb\xbf" + text.encode())
    (folder / "mapping.toml").write_text("\n".join(toml) + "\n")


def investigate_in(
    manifest: dict[str, Any], out: Path, table: str, record: str
) -> tuple[str, str | None, str | None]:
    digest = manifest["snapshot"]["content_sha256"]
    request = InvestigationRequest(
        "req_test", digest, RecordRef(digest, table, record), "check", CUTOFF, "p"
    )
    d = investigate(out / "snapshot.sqlite", request).diagnosis
    return d.disposition.value, d.category, d.reason


def expected(name: str) -> tuple[str, str | None, str | None]:
    e = EXPECTED[name]
    return e["disposition"], e["category"], e.get("abstention_reason")


@pytest.mark.parametrize(
    "name",
    [
        "f06_double_payment",
        "f12_payment_missing_from_bank",
        "pending_settlement",
        "weekend_clearing",
        "real_partial_payment",
        "f06_second_debit_post_cutoff",
    ],
)
def test_rewritten_fixture_gives_the_expected_outcome(
    name: str, tmp_path: Path
) -> None:
    write_bundle(name, tmp_path / "bundle")
    manifest = prepare_csv_bundle(
        tmp_path / "bundle" / "mapping.toml", tmp_path / "out", cutoff=CUTOFF
    )
    p = EXPECTED[name]["primary_entity"]
    assert investigate_in(manifest, tmp_path / "out", p["table"], p["id"]) == expected(
        name
    )
    assert manifest["format"] == "csv_bundle"
    assert manifest["tables_not_supplied"][0] == "vendors"
    assert "payments" not in manifest["tables_not_supplied"]


def test_rewrite_and_original_store_the_same_values(tmp_path: Path) -> None:
    write_bundle("f06_double_payment", tmp_path / "bundle")
    prepare_csv_bundle(
        tmp_path / "bundle" / "mapping.toml", tmp_path / "out", cutoff=CUTOFF
    )
    con = sqlite3.connect(tmp_path / "out" / "snapshot.sqlite")
    got = con.execute(
        "SELECT payment_id, payment_date, payment_amount, payment_status, created_at "
        "FROM payments ORDER BY 1"
    ).fetchall()
    con.close()
    assert got[0] == (
        "PAY_1",
        "2026-06-01",
        "1000.00",
        "completed",
        "2026-06-01T08:00:00",
    )


def test_example_bundle_answers_one_case_and_abstains_on_another(
    tmp_path: Path,
) -> None:
    manifest = prepare_csv_bundle(
        EXAMPLE / "mapping.toml", tmp_path / "out", cutoff=CUTOFF
    )
    out = tmp_path / "out"
    assert investigate_in(manifest, out, "invoices", "INV-2001") == (
        "answered",
        "F06",
        None,
    )
    assert investigate_in(manifest, out, "invoices", "INV-2002") == (
        "abstained",
        None,
        "commitment_not_proven_at_cutoff",
    )
    assert "approval_events" in manifest["tables_not_supplied"]
    assert manifest["extras"]["rows_read"]["payments"] == 4
    assert set(manifest["source_file_sha256"]) >= {"pay_export.csv", "bank_export.csv"}
    con = sqlite3.connect(out / "snapshot.sqlite")
    # signed amount: stored absolute, direction from the stated sign; IBAN and
    # reference text normalised so the two exports' spellings agree
    assert con.execute(
        "SELECT amount, direction, counterparty_token, payment_reference "
        "FROM bank_transactions WHERE bank_transaction_id='BK-001'"
    ).fetchone() == ("1000.00", "debit", "DE89370400440532013000", "INV-2001 RUN 7")
    assert con.execute(
        "SELECT bank_account_id, payment_amount FROM payments WHERE payment_id='P-0001'"
    ).fetchone() == ("DE89370400440532013000", "1000.00")
    con.close()


# --- refusals -------------------------------------------------------------


def bundle(tmp_path: Path, name: str = "f06_double_payment") -> Path:
    write_bundle(name, tmp_path / "bundle")
    return tmp_path / "bundle"


def edit(path: Path, old: str, new: str) -> None:
    raw = path.read_bytes().decode()
    assert old in raw, old
    path.write_bytes(raw.replace(old, new, 1).encode())


def prepare_in(folder: Path, tmp_path: Path) -> dict[str, Any]:
    return prepare_csv_bundle(folder / "mapping.toml", tmp_path / "out", cutoff=CUTOFF)


def refused(folder: Path, tmp_path: Path, match: str) -> str:
    with pytest.raises(ValueError, match=match) as e:
        prepare_in(folder, tmp_path)
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".prepare-*"))
    return str(e.value)


def test_bank_export_ending_before_the_cutoff_is_refused(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "mapping.toml", "2026-07-31", "2026-06-29")
    refused(folder, tmp_path, "cutoff 2026-06-30 is after bank_data_through 2026-06-29")


def test_bank_data_through_is_required(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "mapping.toml", 'bank_data_through = "2026-07-31"', "")
    refused(folder, tmp_path, "bank_data_through: is required")


@pytest.mark.parametrize(
    "key",
    [
        "bank_data_from",
        "all_paying_accounts_covered",
        "reference_matching",
        "fee_journals_typed",
    ],
)
def test_every_declaration_is_required_and_explained(key: str, tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    text = (folder / "mapping.toml").read_text()
    kept = [line for line in text.splitlines() if not line.startswith(key)]
    (folder / "mapping.toml").write_text("\n".join(kept) + "\n")
    refused(folder, tmp_path, f"{key}: is required")


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            "all_paying_accounts_covered = true",
            "all_paying_accounts_covered = false",
            "statements for every account payments are made from",
        ),
        (
            "all_paying_accounts_covered = true",
            'all_paying_accounts_covered = "yes"',
            "all_paying_accounts_covered: must be true",
        ),
        (
            'reference_matching = "normalised"',
            'reference_matching = "fuzzy"',
            "reference_matching: must be one of",
        ),
        ("fee_journals_typed = true", 'fee_journals_typed = "yes"', "true or false"),
        (
            'bank_data_through = "2026-07-31"',
            "bank_data_through = 2026-07-31T10:00:00",
            "bank_data_through: must be a date",
        ),
        ('bank_data_from = "2026-01-01"', "bank_data_from = 5", "must be a date"),
        (
            'bank_data_from = "2026-01-01"',
            'bank_data_from = "2026-08-01"',
            "bank_data_from 2026-08-01 is after",
        ),
    ],
)
def test_declarations_are_checked(
    old: str, new: str, match: str, tmp_path: Path
) -> None:
    folder = bundle(tmp_path)
    edit(folder / "mapping.toml", old, new)
    refused(folder, tmp_path, match)


def test_the_declarations_reach_the_manifest(tmp_path: Path) -> None:
    write_bundle("f06_double_payment", tmp_path / "b", matching="exact")
    manifest = prepare_csv_bundle(
        tmp_path / "b" / "mapping.toml", tmp_path / "out", cutoff=CUTOFF
    )
    assert manifest["bank_data_from"] == "2026-01-01"
    assert manifest["all_paying_accounts_covered"] is True
    assert manifest["reference_matching"] == "exact"
    assert manifest["fee_journals_recognised"] is True


def test_ambiguous_and_unmatched_dates_are_all_reported_with_file_line_value(
    tmp_path: Path,
) -> None:
    folder = bundle(tmp_path)
    edit(
        folder / "mapping.toml",
        'payment_date = { column = "Src payment_date", date_formats = ["%d/%m/%Y"] }',
        'payment_date = { column = "Src payment_date", '
        'date_formats = ["%d/%m/%Y", "%m/%d/%Y"] }',
    )
    pay = folder / "payments_x.csv"
    edit(pay, "01/06/2026", "03/04/2026")  # line 2: 3 April or 4 March
    edit(pay, "08/06/2026", "31/02/2026")  # line 3: no such day in either format
    message = refused(folder, tmp_path, r"2 problem\(s\)")
    assert "payments_x.csv line 2" in message and "'03/04/2026'" in message
    assert "payments_x.csv line 3" in message and "'31/02/2026'" in message


def test_formats_that_agree_on_a_value_are_accepted(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(
        folder / "mapping.toml",
        'payment_date = { column = "Src payment_date", date_formats = ["%d/%m/%Y"] }',
        'payment_date = { column = "Src payment_date", '
        'date_formats = ["%d/%m/%Y", "%m/%d/%Y"] }',
    )
    edit(folder / "payments_x.csv", "01/06/2026", "05/05/2026")  # same date either way
    edit(folder / "payments_x.csv", "08/06/2026", "09/09/2026")
    manifest = prepare_in(folder, tmp_path)
    con = sqlite3.connect(tmp_path / "out" / "snapshot.sqlite")
    assert ("2026-05-05",) in con.execute("SELECT payment_date FROM payments")
    con.close()
    assert manifest["tables"]["payments"]["rows_after"] == 2


def test_a_date_column_must_declare_formats(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(
        folder / "mapping.toml",
        'payment_date = { column = "Src payment_date", date_formats = ["%d/%m/%Y"] }',
        'payment_date = "Src payment_date"',
    )
    refused(folder, tmp_path, "payment_date: a date column must declare 'date_formats'")


def test_unmapped_status_is_refused_with_value_and_line(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "payments_x.csv", "<completed>", "Zombie")
    message = refused(folder, tmp_path, "not in the 'values' map")
    assert "'Zombie'" in message and "payments_x.csv" in message and "line 2" in message


def test_conflicting_rows_with_one_key_are_refused_naming_both_lines(
    tmp_path: Path,
) -> None:
    folder = bundle(tmp_path)
    pay = folder / "payments_x.csv"
    text = pay.read_bytes().decode()
    first = text.splitlines()[1]
    changed = first.replace("1.000,00", "999,00")
    assert changed != first
    pay.write_bytes((text.rstrip("\r\n") + "\r\n" + changed + "\r\n").encode())
    message = refused(folder, tmp_path, "conflicting rows")
    assert "payments_x.csv line 2" in message and "payments_x.csv line 4" in message


def test_exact_repeat_rows_are_removed(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    pay = folder / "payments_x.csv"
    text = pay.read_bytes().decode()
    pay.write_bytes(
        (text.rstrip("\r\n") + "\r\n" + text.splitlines()[1] + "\r\n").encode()
    )
    manifest = prepare_in(folder, tmp_path)
    assert manifest["tables"]["payments"]["exact_duplicates_removed"] == 1


@pytest.mark.parametrize("name", ["../outside.csv", "/etc/hostname", "sub/../../x.csv"])
def test_a_mapping_path_cannot_leave_its_folder(name: str, tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    (tmp_path / "outside.csv").write_text("a\n")
    edit(folder / "mapping.toml", 'file = "payments_x.csv"', f'file = "{name}"')
    refused(folder, tmp_path, "relative path inside the mapping's folder")


def test_a_symlink_cannot_leave_the_folder(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n")
    link = folder / "link.csv"
    link.symlink_to(secret)
    edit(folder / "mapping.toml", 'file = "payments_x.csv"', 'file = "link.csv"')
    refused(folder, tmp_path, "leaves the mapping's folder")


def test_a_mapping_in_a_subfolder_resolves_relative_to_itself(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    sub = folder / "inner"
    sub.mkdir()
    for f in folder.glob("*.csv"):
        f.rename(sub / f.name)
    (folder / "mapping.toml").rename(sub / "mapping.toml")
    prepare_in(sub, tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("version = 1", "version = 2", "version: must be 1"),
        ("version = 1", "version = 1\nsurprise = 1", "unknown keys.*surprise"),
        (
            'payment_id = "Src payment_id"',
            'payment_id = "Src payment_id"\nnot_a_column = "x"',
            "unknown contract columns.*not_a_column",
        ),
        (
            'payment_id = "Src payment_id"',
            'payment_id = { column = "Src payment_id", colour = "red" }',
            "unknown keys.*colour",
        ),
        (
            'payment_id = "Src payment_id"',
            'payment_id = { column = "Src payment_id", value = "x" }',
            "exactly one of 'column' and 'value'",
        ),
        (
            'payment_id = "Src payment_id"',
            'payment_id = "No such header"',
            "source columns missing from the header.*No such header",
        ),
        (
            'decimal_separator = ",", thousands_separator = "." }',
            'decimal_separator = ",", thousands_separator = "," }',
            "single, different characters",
        ),
        (
            'decimal_separator = ",", thousands_separator = "." }',
            'decimal_separator = 1, thousands_separator = "." }',
            "'decimal_separator' must be text",
        ),
        (
            'decimal_separator = ",", thousands_separator = "." }',
            'decimal_separator = ",", thousands_separator = 2 }',
            "'thousands_separator' must be text",
        ),
        (
            'payment_id = "Src payment_id"',
            'payment_id = { column = "Src payment_id", direction_from_sign = "yes" }',
            "'direction_from_sign' must be true or false",
        ),
        (
            'payment_status = { column = "Src payment_status", '
            'values = { "<completed>" = "completed" } }',
            'payment_status = { column = "Src payment_status", '
            'values = { "<completed>" = "completed" }, date_formats = ["%Y"] }',
            "'date_formats' is only for date columns",
        ),
    ],
)
def test_mapping_errors_are_refused(
    old: str, new: str, match: str, tmp_path: Path
) -> None:
    folder = bundle(tmp_path)
    mapping = folder / "mapping.toml"
    text = mapping.read_text()
    if old not in text:  # the status mapping differs by fixture; build it
        old = text[text.index("payment_status = {") :].splitlines()[0]
        new = new.replace('"<completed>" = "completed"', old.split("values = ")[1][:-2])
    edit(mapping, old, new)
    refused(folder, tmp_path, match)


def test_an_unmapped_required_column_is_named(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    mapping = folder / "mapping.toml"
    lines = [
        line
        for line in mapping.read_text().splitlines()
        if not line.startswith("direction")
    ]
    mapping.write_text("\n".join(lines) + "\n")
    message = refused(folder, tmp_path, "required contract columns not mapped")
    assert "direction" in message and "bank_transactions" in message


def test_an_unmapped_optional_column_is_stored_null(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    mapping = folder / "mapping.toml"
    lines = [
        line
        for line in mapping.read_text().splitlines()
        if not line.startswith("payment_method")
    ]
    mapping.write_text("\n".join(lines) + "\n")
    prepare_in(folder, tmp_path)
    con = sqlite3.connect(tmp_path / "out" / "snapshot.sqlite")
    assert con.execute("SELECT DISTINCT payment_method FROM payments").fetchall() == [
        (None,)
    ]
    con.close()


def test_a_required_table_must_be_mapped(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    text = (folder / "mapping.toml").read_text()
    start = text.index("[tables.gl_entries]")
    end = text.index("[tables.approval_events]")
    (folder / "mapping.toml").write_text(text[:start] + text[end:])
    refused(folder, tmp_path, "required tables not mapped.*gl_entries")


def test_wrong_field_count_is_reported_with_the_line(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    pay = folder / "payments_x.csv"
    text = pay.read_bytes().decode().splitlines()
    text[2] += ";extra"
    pay.write_bytes(("\r\n".join(text) + "\r\n").encode())
    message = refused(folder, tmp_path, "payments_x.csv line 3: 12 fields, expected 11")
    assert "problem" in message


def test_malformed_amount_names_the_line(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "payments_x.csv", "1.000,00", "1.2.3,00")
    message = refused(folder, tmp_path, "is not a number")
    assert "payments_x.csv line 2" in message and "'1.2.3,00'" in message


def test_empty_required_cell_names_the_line(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "payments_x.csv", "<completed>;<settled>", ";<settled>")
    refused(folder, tmp_path, r"payments_x.csv line 2: payment_status is empty")


def test_amount_with_too_many_decimals_names_the_line(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    edit(folder / "payments_x.csv", "1.000,00", "1.000,005")
    refused(folder, tmp_path, r"payments_x.csv line 2.*more than 2 decimal places")


def test_non_utf8_file_is_refused(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    (folder / "payments_x.csv").write_bytes(b"\xff\xfe\x00bad")
    refused(folder, tmp_path, "payments_x.csv: cannot read as CSV")


def test_a_cap_limits_the_listing_and_states_the_count(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    pay = folder / "payments_x.csv"
    lines = pay.read_bytes().decode().splitlines()
    bad = [lines[1].replace("01/06/2026", "99/99/9999") for _ in range(40)]
    pay.write_bytes(("\r\n".join([lines[0], *bad]) + "\r\n").encode())
    message = refused(folder, tmp_path, r"80 problem\(s\)")
    assert "and 55 more" in message


# --- sign convention --------------------------------------------------------


def signed_bundle(tmp_path: Path, debit_sign: str) -> Path:
    folder = bundle(tmp_path)
    mapping = folder / "mapping.toml"
    text = mapping.read_text()
    amount = text[text.index('amount = { column = "Src amount"') :].splitlines()[0]
    new = (
        'amount = { column = "Src amount", direction_from_sign = true, '
        f'debit_sign = "{debit_sign}", decimal_separator = ",", '
        'thousands_separator = "." }'
    )
    text = text.replace(amount, new)
    text = "\n".join(
        line for line in text.splitlines() if not line.startswith("direction = ")
    )
    mapping.write_text(text + "\n")
    bank = folder / "bank_transactions_x.csv"
    raw = bank.read_bytes().decode().replace("1.000,00", "-1.000,00")
    bank.write_bytes(raw.encode())
    return folder


def test_debit_sign_is_stated_and_decides_the_direction(tmp_path: Path) -> None:
    folder = signed_bundle(tmp_path, "negative")
    manifest = prepare_in(folder, tmp_path)
    digest = manifest["snapshot"]["content_sha256"]
    assert digest
    con = sqlite3.connect(tmp_path / "out" / "snapshot.sqlite")
    assert con.execute(
        "SELECT DISTINCT direction, amount FROM bank_transactions"
    ).fetchall() == [("debit", "1000.00")]
    con.close()
    assert investigate_in(manifest, tmp_path / "out", "invoices", "INV_1") == (
        "answered",
        "F06",
        None,
    )


def test_the_opposite_sign_convention_gives_credits_and_no_answer(
    tmp_path: Path,
) -> None:
    folder = signed_bundle(tmp_path, "positive")
    manifest = prepare_in(folder, tmp_path)
    con = sqlite3.connect(tmp_path / "out" / "snapshot.sqlite")
    assert con.execute(
        "SELECT DISTINCT direction FROM bank_transactions"
    ).fetchall() == [("credit",)]
    con.close()
    disposition, category, _ = investigate_in(
        manifest, tmp_path / "out", "invoices", "INV_1"
    )
    assert category != "F06" and disposition != "answered"


def test_direction_from_sign_needs_a_stated_sign(tmp_path: Path) -> None:
    folder = signed_bundle(tmp_path, "negative")
    edit(folder / "mapping.toml", 'debit_sign = "negative", ', "")
    refused(folder, tmp_path, "say which sign is a debit")


def test_zero_amount_has_no_sign_to_read(tmp_path: Path) -> None:
    folder = signed_bundle(tmp_path, "negative")
    edit(folder / "bank_transactions_x.csv", "-1.000,00", "0,00")
    refused(folder, tmp_path, "has no sign, so no direction")


def test_direction_cannot_be_mapped_twice(tmp_path: Path) -> None:
    folder = signed_bundle(tmp_path, "negative")
    edit(
        folder / "mapping.toml",
        "[tables.bank_transactions.columns]",
        '[tables.bank_transactions.columns]\ndirection = { value = "debit" }',
    )
    refused(folder, tmp_path, "do not map it too")
