"""A folder of CSV exports plus a TOML mapping to a prepared snapshot.

The mapping says, per contract table, which file holds it and where each
contract column comes from (see examples/csv_bundle/mapping.toml). Nothing is
guessed: a date must match a declared format, a status must be in the declared
value map, a number must fit the declared separators, and the sign convention
of a signed amount must be stated. Anything else stops preparation with every
offending file, line and value (up to a cap), so one run shows the whole job.

Cells are stripped of surrounding whitespace, a UTF-8 byte-order mark is
accepted, and rows whose cells are all empty (spreadsheet padding) are skipped.
The rows then go through `prepare_operational_snapshot` unchanged.
"""

import csv
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from reconcile.adapters.finrca import TABLES
from reconcile.adapters.inputs import Problems, inside, sha256_file
from reconcile.adapters.operational import (
    REFERENCE_RULES,
    REQUIRED_TABLES,
    optional_columns,
    prepare_operational_snapshot,
)

MAPPING_VERSION = 1
_TOP_KEYS = {
    "version",
    "bank_data_from",
    "bank_data_through",
    "all_paying_accounts_covered",
    "reference_matching",
    "fee_journals_typed",
    "tables",
}
_TABLE_KEYS = {"file", "columns", "delimiter"}
_SPEC_KEYS = {
    "column",
    "value",
    "date_formats",
    "decimal_separator",
    "thousands_separator",
    "values",
    "direction_from_sign",
    "debit_sign",
    "blank_as",
}
_MONEY = ("money", "money_of")
_TIME = ("date", "timestamp")


@dataclass(frozen=True)
class _Field:
    name: str  # contract column
    kind: str
    source: str | None
    constant: str | None
    formats: tuple[str, ...]
    decimal: str
    thousands: str
    values: dict[str, str] | None
    debit_sign: str | None  # set when the sign of this amount gives `direction`
    blank_as: str | None


def _fail(where: str, message: str) -> ValueError:
    return ValueError(f"mapping {where}: {message}")


def _bank_date(key: str, value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value  # TOML's own date literal
    if isinstance(value, str):
        try:
            if date.fromisoformat(value).isoformat() == value:
                return date.fromisoformat(value)
        except ValueError:
            pass
    raise _fail(key, "must be a date such as 2026-06-30")


def _field(table: str, col: str, spec: object) -> _Field:
    where = f"tables.{table}.columns.{col}"
    kind = next(c.kind for c in TABLES[table].columns if c.name == col)
    if isinstance(spec, str):
        spec = {"column": spec}
    if not isinstance(spec, dict):
        raise _fail(where, "must be a source column name or a table of options")
    unknown = sorted(set(spec) - _SPEC_KEYS)
    if unknown:
        raise _fail(where, f"unknown keys {unknown}")
    if ("column" in spec) == ("value" in spec):
        raise _fail(where, "give exactly one of 'column' and 'value'")
    if "value" in spec and set(spec) - {"value"}:
        raise _fail(where, "a constant 'value' takes no other options")
    for key in ("column", "value", "blank_as", "debit_sign"):
        if key in spec and not isinstance(spec[key], str):
            raise _fail(where, f"'{key}' must be text")
    for key in ("decimal_separator", "thousands_separator"):
        if key in spec and not isinstance(spec[key], str):
            raise _fail(where, f"'{key}' must be text")
    if "direction_from_sign" in spec and not isinstance(
        spec["direction_from_sign"], bool
    ):
        raise _fail(where, "'direction_from_sign' must be true or false")
    formats = spec.get("date_formats", [])
    if not (isinstance(formats, list) and all(isinstance(f, str) for f in formats)):
        raise _fail(where, "'date_formats' must be a list of strptime formats")
    if "column" in spec and kind in _TIME and not formats:
        raise _fail(where, "a date column must declare 'date_formats'")
    if formats and kind not in _TIME:
        raise _fail(where, "'date_formats' is only for date columns")
    if kind == "timestamp" and not all("%H" in f for f in formats):
        raise _fail(where, "a timestamp format must include the time (%H)")
    decimal = spec.get("decimal_separator", ".")
    thousands = spec.get("thousands_separator", "")
    separators = ("decimal_separator" in spec) or ("thousands_separator" in spec)
    if separators and kind not in _MONEY:
        raise _fail(where, "separators are only for amount columns")
    if len(decimal) != 1 or len(thousands) > 1 or decimal == thousands:
        raise _fail(where, "separators must be single, different characters")
    mapped = spec.get("values")
    if mapped is not None:
        ok = isinstance(mapped, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in mapped.items()
        )
        if not ok or kind in _TIME or kind in _MONEY:
            raise _fail(where, "'values' must map text to text, on a text column")
    sign = None
    if spec.get("direction_from_sign"):
        sign = spec.get("debit_sign")
        if (table, col) != ("bank_transactions", "amount"):
            raise _fail(
                where, "direction_from_sign is only for bank_transactions.amount"
            )
        if sign not in ("negative", "positive"):
            raise _fail(
                where,
                "say which sign is a debit: debit_sign = 'negative' or 'positive'",
            )
    elif "debit_sign" in spec or "direction_from_sign" in spec:
        raise _fail(where, "debit_sign needs direction_from_sign = true")
    return _Field(
        col,
        kind,
        spec.get("column"),
        spec.get("value"),
        tuple(formats),
        decimal,
        thousands,
        mapped,
        sign,
        spec.get("blank_as"),
    )


def _table_fields(name: str, cfg: object) -> tuple[dict[str, _Field], str]:
    where = f"tables.{name}"
    if not isinstance(cfg, dict):
        raise _fail(where, "must be a table")
    unknown = sorted(set(cfg) - _TABLE_KEYS)
    if unknown:
        raise _fail(where, f"unknown keys {unknown}")
    delimiter = cfg.get("delimiter", ",")
    if not (isinstance(delimiter, str) and len(delimiter) == 1):
        raise _fail(f"{where}.delimiter", "must be one character")
    columns = cfg.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise _fail(where, "needs a [columns] table")
    known = {c.name for c in TABLES[name].columns}
    bad = sorted(set(columns) - known)
    if bad:
        raise _fail(where, f"unknown contract columns {bad}; known: {sorted(known)}")
    fields = {col: _field(name, col, spec) for col, spec in columns.items()}
    from_sign = any(f.debit_sign for f in fields.values())
    if from_sign:
        if "direction" in fields:
            raise _fail(where, "direction is filled from the sign; do not map it too")
        fields["direction"] = _Field(
            "direction", "keep", None, None, (), ".", "", None, None, None
        )
    missing = sorted(known - set(fields) - optional_columns(name))
    if missing:
        raise _fail(
            where,
            f"required contract columns not mapped: {missing}. A report built "
            "without them could reach a wrong conclusion, so map them or "
            "supply a constant with { value = ... }",
        )
    return fields, delimiter


def _number(raw: str, f: _Field) -> str | None:
    """The plain decimal text for `raw` under the field's separators, or None."""
    d = re.escape(f.decimal)
    if f.thousands:
        t = re.escape(f.thousands)
        pattern = rf"[+-]?(?:\d{{1,3}}(?:{t}\d{{3}})+|\d+)(?:{d}\d+)?"
    else:
        pattern = rf"[+-]?\d+(?:{d}\d+)?"
    if not re.fullmatch(pattern, raw, re.ASCII):
        return None
    if f.thousands:
        raw = raw.replace(f.thousands, "")
    return raw.replace(f.decimal, ".")


def _when(raw: str, f: _Field) -> tuple[str | None, str]:
    """(ISO text, "") for `raw`, or (None, why) when no declared format matches
    or the formats that match give different dates."""
    found: dict[str, str] = {}
    for fmt in f.formats:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        found[fmt] = (
            parsed.isoformat() if f.kind == "timestamp" else parsed.date().isoformat()
        )
    if not found:
        return None, f"matches none of {list(f.formats)}"
    if len(set(found.values())) > 1:
        return None, f"is ambiguous, the formats give different dates: {found}"
    return next(iter(found.values())), ""


def _read_table(
    path: Path,
    rel: str,
    fields: dict[str, _Field],
    delimiter: str,
    problems: Problems,
) -> tuple[list[dict[str, str | None]], list[int]]:
    rows: list[dict[str, str | None]] = []
    lines: list[int] = []
    unmapped: dict[tuple[str, str], list[int]] = {}
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=delimiter, strict=True)
            header = [h.strip() for h in next(reader, [])]
            if not header:
                raise ValueError(f"{rel}: empty file")
            if len(set(header)) != len(header):
                raise ValueError(f"{rel}: duplicate column names in header")
            wanted = {fl.source for fl in fields.values() if fl.source}
            missing = sorted(wanted - set(header))
            if missing:
                raise ValueError(
                    f"{rel}: source columns missing from the header: {missing}; "
                    f"header is {header}"
                )
            at = {h: i for i, h in enumerate(header)}
            for cells in reader:
                if not any(c.strip() for c in cells):
                    continue
                n = reader.line_num
                if len(cells) != len(header):
                    problems.add(
                        f"{rel} line {n}: {len(cells)} fields, expected {len(header)}"
                    )
                    continue
                row: dict[str, str | None] = {}
                for fl in fields.values():
                    _extract(fl, cells, at, row, rel, n, problems, unmapped)
                rows.append(row)
                lines.append(n)
    except (csv.Error, UnicodeDecodeError) as e:
        raise ValueError(f"{rel}: cannot read as CSV (UTF-8): {e}") from e
    for (column, value), where in sorted(unmapped.items()):
        problems.add(
            f"{rel} column {column!r}: value {value!r} is not in the 'values' map "
            f"({len(where)} row(s), first at line {where[0]})"
        )
    return rows, lines


def _extract(
    fl: _Field,
    cells: list[str],
    at: dict[str, int],
    row: dict[str, str | None],
    rel: str,
    n: int,
    problems: Problems,
    unmapped: dict[tuple[str, str], list[int]],
) -> None:
    if fl.source is None and fl.constant is None:
        return  # `direction`, filled from the sign of the amount
    if fl.constant is not None:
        row[fl.name] = fl.constant
        return
    assert fl.source is not None
    raw = cells[at[fl.source]].strip()
    if not raw and fl.blank_as is not None:
        raw = fl.blank_as
    where = f"{rel} line {n}: {fl.source!r}"
    if fl.values is not None:
        if raw in fl.values:
            row[fl.name] = fl.values[raw]
        elif not raw:
            row[fl.name] = (
                None  # a blank is not a value to map; required columns still fail
            )
        else:
            unmapped.setdefault((fl.source, raw), []).append(n)
        return
    if not raw:
        row[fl.name] = None
    elif fl.kind in _TIME:
        iso, why = _when(raw, fl)
        if iso is None:
            problems.add(f"{where} = {raw!r} {why}")
        row[fl.name] = iso
    elif fl.kind in _MONEY:
        plain = _number(raw, fl)
        if plain is None:
            problems.add(
                f"{where} = {raw!r} is not a number with decimal "
                f"{fl.decimal!r} and thousands {fl.thousands!r}"
            )
            return
        if fl.debit_sign:
            negative = plain.startswith("-")
            magnitude = plain.lstrip("+-")
            if not magnitude.strip("0."):
                problems.add(f"{where} = {raw!r} has no sign, so no direction")
                return
            is_debit = negative == (fl.debit_sign == "negative")
            row["direction"] = "debit" if is_debit else "credit"
            plain = magnitude
        row[fl.name] = plain
    else:
        row[fl.name] = raw


def prepare_csv_bundle(
    mapping_path: Path, out_dir: Path, *, cutoff: date
) -> dict[str, Any]:
    """Read the mapping and the CSV files it names (inside its folder), and
    write a snapshot into the new directory `out_dir`. Returns the manifest."""
    base = mapping_path.resolve().parent
    with mapping_path.open("rb") as f:
        mapping = tomllib.load(f)
    unknown = sorted(set(mapping) - _TOP_KEYS)
    if unknown:
        raise _fail("top level", f"unknown keys {unknown}")
    if mapping.get("version") != MAPPING_VERSION:
        raise _fail("version", f"must be {MAPPING_VERSION}")
    required = {
        "bank_data_from": "is required: the first day the bank export covers, so a "
        "payment made before it is not reported as missing from the bank",
        "bank_data_through": "is required: the date the bank export runs to, so a "
        "payment made after it is not reported as missing from the bank",
        "all_paying_accounts_covered": "is required: write "
        "all_paying_accounts_covered = true once the bank export holds every "
        "account payments are made from (and false or absent means it does not)",
        "reference_matching": "is required: 'exact' (references equal after "
        "trimming) or 'normalised' (also ignoring case and inner spacing)",
        "fee_journals_typed": "is required: true when every bank-fee journal in "
        "the GL file is mapped to the type bank_fee and sourced to its bank row, "
        "false when fee journals cannot be recognised in this data",
    }
    for key, why in required.items():
        if key not in mapping:
            raise _fail(key, why)
    start = _bank_date("bank_data_from", mapping["bank_data_from"])
    through = _bank_date("bank_data_through", mapping["bank_data_through"])
    if mapping["all_paying_accounts_covered"] is not True:
        raise _fail(
            "all_paying_accounts_covered",
            "must be true: bank statements for every account payments are made "
            "from must be supplied, or a payment paid from another account would "
            "look missing from the bank",
        )
    matching = mapping["reference_matching"]
    if not isinstance(matching, str) or matching not in REFERENCE_RULES:
        raise _fail("reference_matching", f"must be one of {list(REFERENCE_RULES)}")
    fee_typed = mapping["fee_journals_typed"]
    if not isinstance(fee_typed, bool):
        raise _fail("fee_journals_typed", "must be true or false")
    configs = mapping.get("tables")
    if not isinstance(configs, dict):
        raise _fail("tables", "needs at least the required tables")
    unknown_tables = sorted(set(configs) - set(TABLES))
    if unknown_tables:
        raise _fail("tables", f"unknown tables {unknown_tables}; known: {list(TABLES)}")
    absent = [t for t in REQUIRED_TABLES if t not in configs]
    if absent:
        raise _fail("tables", f"required tables not mapped: {absent}")

    problems = Problems("CSV files", "row")
    tables: dict[str, list[dict[str, str | None]]] = {}
    lines: dict[str, list[int]] = {}
    files: dict[str, str] = {}
    digests: dict[str, str] = {}
    rows_read: dict[str, int] = {}
    for name in TABLES:
        if name not in configs:
            continue
        cfg = configs[name]
        fields, delimiter = _table_fields(name, cfg)
        path = inside(base, cfg.get("file"), f"tables.{name}.file", owner="mapping")
        rel = path.relative_to(base.resolve()).as_posix()
        files[name] = rel
        digests[rel] = sha256_file(path)
        tables[name], lines[name] = _read_table(path, rel, fields, delimiter, problems)
        rows_read[name] = len(tables[name])
    problems.raise_if_any()

    def locate(table: str, n: int) -> str:
        return f"{files[table]} line {lines[table][n - 1]}"

    return prepare_operational_snapshot(
        tables,
        out_dir,
        cutoff=cutoff,
        bank_data_from=start,
        bank_data_through=through,
        all_paying_accounts_covered=True,
        reference_matching=matching,
        fee_journals_typed=fee_typed,
        source_format="csv_bundle",
        source_files=digests,
        extras={
            "mapping_version": MAPPING_VERSION,
            "mapping_file": mapping_path.name,
            "mapping_sha256": sha256_file(mapping_path),
            "table_files": files,
            "rows_read": rows_read,
        },
        locate=locate,
    )
