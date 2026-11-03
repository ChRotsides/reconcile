"""Xero JSON files plus CAMT.053 XML files to a prepared snapshot.

A small TOML config names the files (inside its own folder) and the few
choices the formats leave open; see fixtures/xero_camt/config.toml.
The readers are in `xero` and `camt053`; the rows then go through
`prepare_operational_snapshot` unchanged.
"""

import hashlib
import tomllib
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from reconcile.adapters.camt053 import (
    DEFAULT_FEE_CODES,
    REFERENCE_KINDS,
    read_camt053,
)
from reconcile.adapters.operational import prepare_operational_snapshot
from reconcile.adapters.xero import KINDS, REFERENCE_FIELDS, read_xero

CONFIG_VERSION = 1
_KEYS = {
    "version",
    "xero_dir",
    "camt_files",
    "base_currency",
    "payment_reference_field",
    "payment_method_by_account",
    "default_payment_method",
    "bank_reference_fields",
    "fee_codes",
    "bank_data_through",
}


def _fail(where: str, message: str) -> ValueError:
    return ValueError(f"config {where}: {message}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(base: Path, name: object, where: str, *, directory: bool = False) -> Path:
    if not isinstance(name, str) or not name:
        raise _fail(where, "must be a path")
    posix = PurePosixPath(name.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or Path(name).is_absolute():
        raise _fail(
            where, f"{name!r} must be a relative path inside the config's folder"
        )
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise _fail(where, f"{name!r} leaves the config's folder")
    if not (path.is_dir() if directory else path.is_file()):
        raise _fail(where, f"{name!r} is not a {'folder' if directory else 'file'}")
    return path


def _text_list(config: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = config.get(key, default)
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise _fail(key, "must be a list of text")
    return value


def _through(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            if date.fromisoformat(value).isoformat() == value:
                return date.fromisoformat(value)
        except ValueError:
            pass
    raise _fail("bank_data_through", "must be a date such as 2026-06-30")


def prepare_xero_camt(
    config_path: Path, out_dir: Path, *, cutoff: date
) -> dict[str, Any]:
    """Read the config and the files it names, and write a snapshot into the new
    directory `out_dir`. Returns the manifest."""
    base = config_path.resolve().parent
    with config_path.open("rb") as f:
        config = tomllib.load(f)
    unknown = sorted(set(config) - _KEYS)
    if unknown:
        raise _fail("top level", f"unknown keys {unknown}")
    if config.get("version") != CONFIG_VERSION:
        raise _fail("version", f"must be {CONFIG_VERSION}")
    for key in ("xero_dir", "camt_files", "base_currency"):
        if key not in config:
            raise _fail(key, "is required")
    currency = config["base_currency"]
    if not isinstance(currency, str):
        raise _fail("base_currency", "must be a currency code such as EUR")
    reference_field = config.get("payment_reference_field", "Reference")
    if reference_field not in REFERENCE_FIELDS:
        raise _fail(
            "payment_reference_field", f"must be one of {list(REFERENCE_FIELDS)}"
        )
    by_account = config.get("payment_method_by_account", {})
    if not (
        isinstance(by_account, dict)
        and all(
            isinstance(k, str) and isinstance(v, str) for k, v in by_account.items()
        )
    ):
        raise _fail("payment_method_by_account", "must map account codes to methods")
    default_method = config.get("default_payment_method")
    if default_method is not None and not isinstance(default_method, str):
        raise _fail("default_payment_method", "must be text")
    bank_fields = _text_list(config, "bank_reference_fields", ["EndToEndId"])
    if not bank_fields or any(f not in REFERENCE_KINDS for f in bank_fields):
        raise _fail(
            "bank_reference_fields", f"must list some of {list(REFERENCE_KINDS)}"
        )
    fee_codes = config.get("fee_codes", [list(c) for c in DEFAULT_FEE_CODES])
    if not (
        isinstance(fee_codes, list)
        and all(
            isinstance(c, list) and len(c) == 3 and all(isinstance(v, str) for v in c)
            for c in fee_codes
        )
    ):
        raise _fail("fee_codes", "must be a list of [domain, family, sub-family] codes")
    declared = _through(config.get("bank_data_through"))

    xero_dir = _inside(base, config["xero_dir"], "xero_dir", directory=True)
    camt_names = config["camt_files"]
    if not (isinstance(camt_names, list) and camt_names):
        raise _fail("camt_files", "must be a non-empty list of files")
    camt_paths = [
        _inside(base, n, f"camt_files[{i}]") for i, n in enumerate(camt_names)
    ]
    xero_files: dict[str, list[Path]] = {}
    for kind in KINDS:
        found = sorted(p for p in xero_dir.glob(f"{kind}*.json") if p.is_file())
        xero_files[kind] = [
            _inside(base, p.relative_to(base).as_posix(), f"xero_dir/{p.name}")
            for p in found
        ]

    xero = read_xero(
        xero_files,
        base_currency=currency,
        reference_field=reference_field,
        method_by_account=by_account,
        default_method=default_method,
    )
    camt = read_camt053(
        camt_paths,
        reference_fields=bank_fields,
        fee_codes=fee_codes,
        bank_data_through=declared,
    )
    assert camt.bank_data_through is not None  # read_camt053 refuses otherwise

    tables = {**xero.tables, "bank_transactions": camt.rows}
    where = {**xero.where, "bank_transactions": camt.where}
    sources = [*camt_paths, *(p for ps in xero_files.values() for p in ps)]
    digests = {p.relative_to(base).as_posix(): _sha256(p) for p in sources}
    return prepare_operational_snapshot(
        tables,
        out_dir,
        cutoff=cutoff,
        bank_data_through=camt.bank_data_through,
        source_format="xero_camt053",
        source_files=digests,
        extras={
            "config_version": CONFIG_VERSION,
            "config_file": config_path.name,
            "config_sha256": _sha256(config_path),
            "xero": xero.extras,
            "camt053": camt.extras,
            "approval_events": (
                "not supplied: neither format carries approvals, so every "
                "all-clear abstains"
            ),
        },
        locate=lambda table, n: where[table][n - 1],
    )
