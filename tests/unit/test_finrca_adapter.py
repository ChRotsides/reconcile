"""FinRCA input transform on tiny invented fixtures (never upstream rows, D030)."""

import csv
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    TABLES,
    create_key_file,
    prepare_finrca_snapshot,
)

CUTOFF = date(2026, 6, 30)
SENTINELS = [
    "SENTINEL_VENDOR_NAME",
    "SENTINEL_MYSTERY",
    "SENTINEL_MEMO",
    "SENTINEL_INVNUM",
    "SENTINEL_BANKREF",
    "SENTINEL_OPENING",
    "SENTINEL_AUDIT",
    "SENTINEL_COMMENT",
]


def _write(src: Path, name: str, header: str, rows: list[str]) -> None:
    (src / f"{name}.csv").write_text("\n".join([header, *rows]) + "\n")


def make_source(src: Path, n_payments: int = 3) -> None:
    src.mkdir(parents=True, exist_ok=True)
    _write(
        src,
        "vendors",
        "vendor_id,vendor_name,vendor_type,tax_id_hash,country,currency,payment_terms,"
        "default_payment_method,bank_account_token,bank_routing_token,vendor_status,"
        "created_at,updated_at,source_system,mystery_column",
        [
            "V_1,SENTINEL_VENDOR_NAME,supplier,h1,DE,EUR,net30,ach,BA_A,R1,active,"
            "2026-01-01T08:00:00,2026-01-01T08:00:00,erp,SENTINEL_MYSTERY",
            "V_2,Other,supplier,h2,JP,JPY,net30,wire,BA_B,R2,active,"
            "2026-01-01T08:00:00,2026-01-01T08:00:00,erp,x",
        ],
    )
    _write(
        src,
        "purchase_orders",
        "po_id,vendor_id,po_date,currency,subtotal,tax,shipping,po_total,department,"
        "cost_center,gl_account,status,expected_delivery_date,created_by,source_system",
        ["PO_1,V_1,2026-02-01,EUR,100,0,0,100,d,c,g,open,2026-03-01,e1,erp"],
    )
    _write(
        src,
        "po_lines",
        "po_id,po_line_id,item_id,description,quantity,unit_price,line_amount,gl_account,"
        "department,cost_center,source_system",
        ["PO_1,POL_1,i,desc,2,50,100.5,g,d,c,erp"],
    )
    _write(
        src,
        "invoices",
        "invoice_id,vendor_id,po_id,invoice_number,invoice_date,received_date,due_date,"
        "currency,subtotal,tax,shipping,invoice_total,payment_terms,status,"
        "duplicate_reference,created_at,source_system",
        [
            "INV_1,V_1,PO_1,SENTINEL_INVNUM,2026-03-01,2026-03-02,2026-04-01,EUR,"
            "100,0,0,100.00,net30,paid,,2026-03-02T08:00:00,erp",
            "INV_2,V_2,,N2,2026-03-01,2026-03-02,2026-04-01,JPY,"
            "1000,0,0,1000,net30,paid,,2026-03-02T08:00:00,erp",
        ],
    )
    _write(
        src,
        "invoice_lines",
        "invoice_id,invoice_line_id,po_line_id,item_id,quantity,unit_price,line_amount,"
        "gl_account,department,cost_center,source_system",
        [
            "INV_1,IL_1,POL_1,i,1,100,100,g,d,c,erp",
            "INV_2,IL_2,,i,1,1000,1000,g,d,c,erp",
        ],
    )
    pay = [
        f"PAY_{i:07d},V_1,2026-05-01,ach,EUR,{i}.50,ACC_{i % 2},completed,settled,"
        f"PMTREF-{i + 42:010d},2026-05-01T08:00:00,erp"
        for i in range(1, n_payments + 1)
    ]
    _write(
        src,
        "payments",
        "payment_id,vendor_id,payment_date,payment_method,payment_currency,payment_amount,"
        "bank_account_id,payment_status,settlement_status,reference_number,created_at,"
        "source_system",
        pay,
    )
    _write(
        src,
        "payment_allocations",
        "payment_id,invoice_id,allocated_amount,allocation_date,source_system",
        ["PAY_0000001,INV_1,1.50,2026-05-01,erp"],
    )
    bank = [
        f"BT_{i:07d},BANK_MAIN,2026-05-01,2026-05-04,credit,{i}.50,EUR,out,"
        f"SENTINEL_BANKREF{i},CP_{i},PMTREF-{i + 42:010d},posted,bank"
        for i in range(1, n_payments + 1)
    ]
    bank += [
        "BT_9000001,BANK_MAIN,2026-06-18,2026-07-01,credit,5.00,EUR,out,B,CP_9,"
        "PMTREF-0000000099,posted,bank",
        "BT_9000002,BANK_MAIN,2026-06-30,2026-06-30,credit,7.00,EUR,out,B,CP_9,"
        "BANKPORTAL-0000001,posted,bank",
    ]
    _write(
        src,
        "bank_transactions",
        "bank_transaction_id,bank_account_id,transaction_date,posted_date,transaction_type,"
        "amount,currency,direction,bank_reference,counterparty_token,payment_reference,"
        "status,source_system",
        bank,
    )
    _write(
        src,
        "bank_statements",
        "bank_statement_id,bank_account_id,statement_date,opening_balance,closing_balance,"
        "total_debits,total_credits,source_system",
        ["ST_1,BANK_MAIN,2026-05-31,SENTINEL_OPENING,1,2,3,bank"],
    )
    _write(
        src,
        "vendor_change_log",
        "change_id,vendor_id,field_changed,old_value,new_value,changed_at,changed_by,"
        "change_reason,source_system",
        [
            "CH_1,V_1,bank_account_token,BA_OLD,BA_A,2026-04-01T08:00:00,e,r,erp",
            "CH_2,V_1,payment_terms,net15,net30,2026-04-02T08:00:00,e,r,erp",
        ],
    )
    _write(
        src,
        "approval_events",
        "approval_event_id,invoice_id,approval_level,approver_id,approver_role,action,"
        "event_timestamp,previous_status,new_status,comments,source_system",
        [
            "AP_1,INV_1,1,E1,manager,approve,2026-03-03T09:00:00,received,approved,"
            "SENTINEL_COMMENT,erp"
        ],
    )
    _write(
        src,
        "gl_entries",
        "journal_id,journal_line_id,transaction_type,source_transaction_id,posting_date,"
        "accounting_period,gl_account,debit,credit,currency,department,cost_center,memo,"
        "source_system",
        [
            "JE_1,JL_1,payment,PAY_0000001,2026-05-01,2026-05,g,1.50,0,EUR,d,c,SENTINEL_MEMO,erp",
            "JE_1,JL_2,payment,PAY_0000001,2026-05-01,2026-05,h,0,1.50,EUR,d,c,m,erp",
            "JE_2,JL_3,invoice,INV_1,2026-03-01,2026-03,g,100,0,EUR,d,c,m,erp",
            "JE_3,JL_4,bank,BT_9000001,2026-07-01,2026-07,g,5,0,EUR,d,c,m,erp",
        ],
    )
    # Dropped tables are never opened; invalid content here proves it.
    (src / "audit_log.csv").write_bytes(b"\xff\xfe SENTINEL_AUDIT\n")
    (src / "employees.csv").write_bytes(b"\xff\xfe SENTINEL_AUDIT\n")


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / "src"
    make_source(src)
    key = tmp_path / "private" / "key"
    key.parent.mkdir()
    create_key_file(key)
    return {"src": src, "out": tmp_path / "prepared", "key": key, "tmp": tmp_path}


def prepare(p: dict[str, Path], out: str = "out", **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "key_path": p["key"],
        "source_revision": "test-rev",
        "cutoff": CUTOFF,
    }
    args.update(kw)
    return prepare_finrca_snapshot(p["src"], p[out], **args)


def rows(p: dict[str, Path], sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(f"file:{p['out'] / 'snapshot.sqlite'}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_only_allowlisted_columns_and_tables(paths: dict[str, Path]) -> None:
    prepare(paths)
    assert {r[0] for r in rows(paths, "SELECT name FROM sqlite_master")} == set(TABLES)
    for name, table in TABLES.items():
        cols = [r[1] for r in rows(paths, f'PRAGMA table_info("{name}")')]
        assert cols == [c.name for c in table.columns]
    blob = b"".join(f.read_bytes() for f in paths["out"].iterdir())
    for sentinel in SENTINELS:
        assert sentinel.encode() not in blob


def test_missing_allowlisted_column_fails(paths: dict[str, Path]) -> None:
    f = paths["src"] / "payments.csv"
    f.write_text(f.read_text().replace("settlement_status", "settlement_state"))
    with pytest.raises(ValueError, match="settlement_status"):
        prepare(paths)


def test_shared_reference_namespace_preserves_matches(paths: dict[str, Path]) -> None:
    prepare(paths)
    matched = rows(
        paths,
        "SELECT count(*) FROM payments p JOIN bank_transactions b "
        "ON p.reference_number = b.payment_reference",
    )
    assert matched == [(3,)]  # BT_9000001 is cut off; BANKPORTAL matches nothing
    unmatched = rows(
        paths,
        "SELECT count(*) FROM bank_transactions b WHERE b.payment_reference NOT IN "
        "(SELECT reference_number FROM payments)",
    )
    assert unmatched == [(1,)]


def test_ids_share_tokens_across_tables_and_namespaces_differ(
    paths: dict[str, Path],
) -> None:
    prepare(paths)
    joins = {
        "payments p JOIN payment_allocations a ON p.payment_id = a.payment_id": 1,
        "payments p JOIN gl_entries g ON p.payment_id = g.source_transaction_id": 2,
        "invoices i JOIN gl_entries g ON i.invoice_id = g.source_transaction_id": 1,
        "invoices i JOIN invoice_lines l ON i.invoice_id = l.invoice_id": 2,
        "vendors v JOIN vendor_change_log c ON v.bank_account_token = c.new_value": 1,
    }
    for sql, expected in joins.items():
        assert rows(paths, f"SELECT count(*) FROM {sql}") == [(expected,)], sql
    # a bank-token change keeps its tokens; other fields lose their free text
    assert rows(
        paths, "SELECT count(*) FROM vendor_change_log WHERE old_value IS NULL"
    ) == [(1,)]
    # the same raw string in two namespaces must not give the same token
    assert rows(
        paths,
        "SELECT count(*) FROM vendors WHERE vendor_id IN "
        "(SELECT po_id FROM purchase_orders)",
    ) == [(0,)]
    # empty reference stays NULL instead of becoming a shared token
    assert rows(paths, "SELECT count(*) FROM invoices WHERE po_id IS NULL") == [(1,)]


def test_tokens_carry_no_order_length_or_format(tmp_path: Path) -> None:
    src = tmp_path / "src"
    make_source(src, n_payments=300)
    key = tmp_path / "key"
    create_key_file(key)
    out = tmp_path / "out"
    prepare_finrca_snapshot(src, out, key_path=key, source_revision="r", cutoff=CUTOFF)
    con = sqlite3.connect(out / "snapshot.sqlite")
    # The fixture ties the amount (i.50) to the raw counter i, so sorting by
    # amount recovers raw order. Stored order is read back by rowid.
    raw_order = con.execute(
        "SELECT payment_id, reference_number FROM payments "
        "ORDER BY CAST(payment_amount AS REAL)"
    ).fetchall()
    stored = con.execute(
        "SELECT payment_id, reference_number FROM payments ORDER BY rowid"
    ).fetchall()
    con.close()
    for column in (0, 1):
        toks = [p[column] for p in raw_order]
        assert {len(t) for t in toks} == {32}
        assert all(set(t) <= set("0123456789abcdef") for t in toks)
        assert len({t[:4] for t in toks}) > 100  # no shared prefix
        ups = sum(a < b for a, b in zip(toks, toks[1:], strict=False))
        assert 0.40 < ups / (len(toks) - 1) < 0.60  # no ascending trend
    # rows are stored sorted by token, which is unrelated to the input order
    assert [p[0] for p in stored] == sorted(p[0] for p in stored)
    assert stored != raw_order


def reverse_rows(src: Path) -> None:
    for f in src.glob("*.csv"):
        if f.stem in ("audit_log", "employees"):
            continue
        header, *body = f.read_text().splitlines()
        f.write_text("\n".join([header, *reversed(body)]) + "\n")


def test_input_row_order_does_not_change_the_snapshot(paths: dict[str, Path]) -> None:
    a = prepare(paths, out="out")
    reverse_rows(paths["src"])
    paths["out2"] = paths["tmp"] / "prepared2"
    b = prepare(paths, out="out2")
    assert a["snapshot"]["content_sha256"] == b["snapshot"]["content_sha256"]


def test_different_key_gives_different_tokens(paths: dict[str, Path]) -> None:
    prepare(paths)
    first = rows(paths, "SELECT payment_id FROM payments ORDER BY payment_id")
    other = paths["tmp"] / "private" / "key2"
    create_key_file(other)
    shutil.rmtree(paths["out"])
    prepare(paths, out="out", key_path=other)
    assert rows(paths, "SELECT payment_id FROM payments ORDER BY payment_id") != first


def test_cutoff_excludes_late_bank_and_gl_rows(paths: dict[str, Path]) -> None:
    m = prepare(paths)
    assert m["tables"]["bank_transactions"] == {
        **m["tables"]["bank_transactions"],
        "rows_before": 5,
        "rows_after": 4,
        "dropped_by_cutoff": 1,
    }
    assert m["tables"]["gl_entries"]["dropped_by_cutoff"] == 1
    assert rows(paths, "SELECT max(posted_date) FROM bank_transactions") == [
        ("2026-06-30",)
    ]
    assert rows(paths, "SELECT max(posting_date) FROM gl_entries") == [("2026-05-01",)]
    # the cutoff is a parameter
    shutil.rmtree(paths["out"])
    later = prepare(paths, out="out", cutoff=date(2026, 7, 1))
    assert later["tables"]["bank_transactions"]["dropped_by_cutoff"] == 0
    assert later["tables"]["gl_entries"]["rows_after"] == 4


def test_money_is_validated_and_canonical(paths: dict[str, Path]) -> None:
    m = prepare(paths)
    assert rows(paths, "SELECT line_amount FROM po_lines") == [
        ("100.50",)
    ]  # padded, exact
    assert rows(paths, "SELECT payment_amount FROM payments ORDER BY payment_amount")[
        0
    ] == ("1.50",)
    assert m["money"]["currencies"] == ["EUR", "JPY"]
    assert m["money"]["max_digits"] == 5  # "100.50" and "100.00"
    assert m["money"]["max_digits_allowed"] == 30


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        ("payments", "EUR,1.50", "EUR,1.505", "payments.csv line 2"),  # too many places
        ("payments", "EUR,1.50", "EUR,1 50", "payments.csv line 2"),  # whitespace
        (
            "invoices",
            "1000,net30",
            "1000.5,net30",
            "invoices.csv line 3",
        ),  # JPY has no decimals
        (
            "bank_transactions",
            ",EUR,out,SENTINEL_BANKREF1",
            ",XXX,out,SENTINEL_BANKREF1",
            "XXX",
        ),
        (
            "gl_entries",
            "1.50,0,EUR,d,c,SENTINEL",
            "1e2,0,EUR,d,c,SENTINEL",
            "gl_entries.csv line 2",
        ),
    ],
)
def test_money_failure_is_loud(
    paths: dict[str, Path], file: str, old: str, new: str, message: str
) -> None:
    f = paths["src"] / f"{file}.csv"
    text = f.read_text()
    assert old in text
    f.write_text(text.replace(old, new, 1))
    with pytest.raises((ValueError, csv.Error), match=message):
        prepare(paths)


def test_unknown_gl_reference_prefix_fails(paths: dict[str, Path]) -> None:
    f = paths["src"] / "gl_entries.csv"
    f.write_text(f.read_text().replace("INV_1,2026-03-01", "ZZZ_1,2026-03-01"))
    with pytest.raises(ValueError, match="unknown reference prefix"):
        prepare(paths)


def test_digest_is_reproducible_and_byte_identical(paths: dict[str, Path]) -> None:
    a = prepare(paths, out="out")
    paths["out2"] = paths["tmp"] / "prepared2"
    b = prepare(paths, out="out2")
    assert a == b
    assert (paths["out"] / "snapshot.sqlite").read_bytes() == (
        paths["out2"] / "snapshot.sqlite"
    ).read_bytes()
    # a changed source changes the digest
    f = paths["src"] / "payments.csv"
    f.write_text(f.read_text().replace("1.50", "1.51", 1))
    shutil.rmtree(paths["out2"])
    c = prepare(paths, out="out2")
    assert c["snapshot"]["content_sha256"] != a["snapshot"]["content_sha256"]


def test_key_is_not_in_the_snapshot_directory(paths: dict[str, Path]) -> None:
    prepare(paths)
    key_hex = paths["key"].read_text().strip()
    for f in paths["out"].iterdir():
        data = f.read_bytes()
        assert key_hex.encode() not in data
        assert bytes.fromhex(key_hex) not in data
    assert {f.name for f in paths["out"].iterdir()} == {
        "snapshot.sqlite",
        "manifest.json",
    }
    shutil.rmtree(paths["out"])
    inside = paths["out"] / "key"
    with pytest.raises(ValueError, match="outside"):
        prepare(paths, key_path=inside)


def test_outputs_are_read_only(paths: dict[str, Path]) -> None:
    prepare(paths)
    for f in paths["out"].iterdir():
        assert f.stat().st_mode & 0o222 == 0
    con = sqlite3.connect(f"file:{paths['out'] / 'snapshot.sqlite'}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM payments")
    con.close()


def test_key_file_is_not_overwritten_and_is_private(tmp_path: Path) -> None:
    k = tmp_path / "k"
    create_key_file(k)
    assert k.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        create_key_file(k)


def test_malformed_csv_fails_instead_of_losing_rows(paths: dict[str, Path]) -> None:
    # An unterminated quote used to swallow every later row without an error.
    (paths["src"] / "vendors.csv").write_text(
        'id,amount,source_system\nP1,1.00,"ERP\nP2,2.00,ERP\nP3,3.00,ERP\n'
    )
    with pytest.raises(csv.Error):
        prepare(paths)
    assert not paths["out"].exists()


def test_duplicate_header_and_wrong_field_count_fail(paths: dict[str, Path]) -> None:
    f = paths["src"] / "payments.csv"
    original = f.read_text()
    f.write_text(original.replace("payment_id,vendor_id", "payment_id,payment_id", 1))
    with pytest.raises(ValueError, match="duplicate column"):
        prepare(paths)
    lines = original.splitlines()
    for bad in (lines[1] + ",extra", lines[1].rsplit(",", 1)[0]):
        f.write_text("\n".join([lines[0], bad, *lines[2:]]) + "\n")
        with pytest.raises(ValueError, match="payments.csv line 2: .* fields"):
            prepare(paths)


def test_exact_duplicate_row_is_a_repeated_import(paths: dict[str, Path]) -> None:
    f = paths["src"] / "payments.csv"
    lines = f.read_text().splitlines()
    f.write_text("\n".join([*lines, lines[1]]) + "\n")
    m = prepare(paths)
    assert m["tables"]["payments"]["exact_duplicates_removed"] == 1
    assert m["tables"]["payments"]["rows_before"] == 4
    assert m["tables"]["payments"]["rows_after"] == 3
    assert rows(paths, "SELECT count(*) FROM payments") == [(3,)]


def test_same_id_with_different_content_is_an_error(paths: dict[str, Path]) -> None:
    f = paths["src"] / "payments.csv"
    lines = f.read_text().splitlines()
    f.write_text("\n".join([*lines, lines[1].replace("1.50", "9.99")]) + "\n")
    with pytest.raises(ValueError, match=r"payments.csv.*PAY_0000001.*lines 2 and 5"):
        prepare(paths)
    assert not paths["out"].exists()


def test_distinct_ids_with_identical_values_both_survive(
    paths: dict[str, Path],
) -> None:
    f = paths["src"] / "payments.csv"
    lines = f.read_text().splitlines()
    twin = (
        lines[1]
        .replace("PAY_0000001", "PAY_0000777")
        .replace("PMTREF-0000000043", "PMTREF-0000000043")
    )  # same amount, vendor, date and reference, different payment ID
    f.write_text("\n".join([*lines, twin]) + "\n")
    m = prepare(paths)
    assert m["tables"]["payments"]["exact_duplicates_removed"] == 0
    assert rows(paths, "SELECT count(*) FROM payments") == [(4,)]
    assert rows(
        paths,
        "SELECT count(DISTINCT reference_number), count(DISTINCT payment_id) "
        "FROM payments",
    ) == [(3, 4)]


def test_existing_output_directory_is_refused(paths: dict[str, Path]) -> None:
    paths["out"].mkdir()
    (paths["out"] / "stray.txt").write_text("x")
    with pytest.raises(FileExistsError):
        prepare(paths)
    assert [f.name for f in paths["out"].iterdir()] == ["stray.txt"]
    with pytest.raises(FileExistsError):
        prepare_finrca_snapshot(
            paths["src"],
            paths["src"],
            key_path=paths["key"],
            source_revision="r",
            cutoff=CUTOFF,
        )


def test_failure_mid_prepare_leaves_nothing_behind(paths: dict[str, Path]) -> None:
    f = paths["src"] / "gl_entries.csv"  # processed last, after other tables
    f.write_text(f.read_text().replace("1.50,0,EUR", "1.505,0,EUR", 1))
    with pytest.raises(ValueError, match="gl_entries.csv"):
        prepare(paths)
    assert not paths["out"].exists()
    assert not [p for p in paths["tmp"].iterdir() if p.name.startswith(".prepare-")]


def test_success_publishes_both_files_together(paths: dict[str, Path]) -> None:
    prepare(paths)
    assert {f.name for f in paths["out"].iterdir()} == {
        "snapshot.sqlite",
        "manifest.json",
    }
    assert not [p for p in paths["tmp"].iterdir() if p.name.startswith(".prepare-")]
