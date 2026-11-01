"""Versioned domain contracts (PLAN.md section 4), the minimum M1 and M2 need.

Plain frozen dataclasses. Each validates itself on construction, so an invalid
object cannot exist, and converts to and from plain JSON objects with strict
parsing: unknown or missing keys, wrong types, floats and unsupported
`schema_version` values are errors. Amounts are `Money` and travel as the
string "<amount> <currency>", never as a number.

Persisted artifacts (`InvestigationRequest`, `EvidencePacket`) carry
`schema_version` and have `to_json` / `from_json`. The other classes are parts
of those and have `to_obj` / `from_obj`.

Left out until a task needs them: `DatasetManifest` and `RunManifest` (M1 uses
the adapter's manifest dict; run manifests start with M3), the organization,
source-file digest and row locator of `RecordRef` (one organization, one
snapshot per run), and non-monetary calculations such as business-day age.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, TypeVar

from reconcile.domain.money import Money

T = TypeVar("T")
SCHEMA_VERSION = "1"
LEGITIMATE = "legitimate"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class UnsupportedSchemaVersionError(ValueError):
    """Raised when a persisted artifact has a schema_version this code can't read."""


class Disposition(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"
    OUT_OF_SCOPE = "out_of_scope"
    FAILED = "failed"


class FindingState(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------- validators


def _text(name: str, v: object) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return v


def _ident(name: str, v: object) -> str:
    if not isinstance(v, str) or not _ID.fullmatch(v):
        raise ValueError(f"{name} must be an identifier, got {v!r}")
    return v


def _sha(name: str, v: object) -> str:
    if not isinstance(v, str) or not _SHA256.fullmatch(v):
        raise ValueError(f"{name} must be a lowercase sha256 hex digest")
    return v


def _type(name: str, v: object, typ: type[T]) -> T:
    if not isinstance(v, typ):
        raise TypeError(f"{name} must be {typ.__name__}, not {type(v).__name__}")
    return v


def _tuple(name: str, v: object, typ: type[T]) -> tuple[T, ...]:
    if not isinstance(v, tuple):
        raise TypeError(f"{name} must be a tuple, not {type(v).__name__}")
    for item in v:
        _type(f"{name} item", item, typ)
    return v


def _unique(name: str, items: tuple[str, ...]) -> None:
    if len(set(items)) != len(items):
        raise ValueError(f"{name} has duplicates")


def _pairs(name: str, v: object) -> tuple[tuple[str, str], ...]:
    """Validate (key, value) string pairs and return them sorted by key."""
    pairs = _tuple(name, v, tuple)
    for p in pairs:
        if len(p) != 2:
            raise ValueError(f"{name} items must be (key, value) pairs")
        _ident(f"{name} key", p[0])
        _type(f"{name} value", p[1], str)
    keys = tuple(p[0] for p in pairs)
    _unique(f"{name} keys", keys)
    return tuple(sorted(pairs))


def _set(obj: object, **fields: object) -> None:
    for k, v in fields.items():
        object.__setattr__(obj, k, v)


def _exact(name: str, obj: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise TypeError(f"{name} must be a JSON object")
    if set(obj) != keys:
        missing, extra = sorted(keys - set(obj)), sorted(set(obj) - keys)
        raise ValueError(f"{name}: missing keys {missing}, unexpected keys {extra}")
    return obj


def _iso_date(name: str, v: object) -> date:
    s = _type(name, v, str)
    d = date.fromisoformat(s)
    if d.isoformat() != s:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {s!r}")
    return d


def _list(name: str, v: object) -> list[Any]:
    return _type(name, v, list)


def _each(name: str, v: object, build: Callable[[Any], T]) -> tuple[T, ...]:
    return tuple(build(x) for x in _list(name, v))


def _strs(name: str, v: object) -> tuple[str, ...]:
    return tuple(_type(f"{name} item", x, str) for x in _list(name, v))


def _check_version(obj: object) -> None:
    if not isinstance(obj, dict) or "schema_version" not in obj:
        raise ValueError("missing schema_version")
    if obj["schema_version"] != SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"unsupported schema_version {obj['schema_version']!r}; "
            f"this code reads only {SCHEMA_VERSION!r}"
        )


def _reject_float(text: str) -> object:
    raise ValueError(f"floats are not allowed in persisted artifacts: {text}")


def _reject_constant(text: str) -> object:
    raise ValueError(f"{text} is not allowed in persisted artifacts")


def _no_duplicate_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in items:
        if k in out:
            raise ValueError(f"duplicate JSON key {k!r}")
        out[k] = v
    return out


def _loads(text: str) -> object:
    return json.loads(
        text,
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_no_duplicate_keys,
    )


def _dumps(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ------------------------------------------------------------------ records


@dataclass(frozen=True, slots=True)
class RecordRef:
    """A row, qualified by snapshot and table. Equal IDs in different tables or
    snapshots are different records."""

    snapshot: str  # snapshot content digest
    table: str
    record_id: str  # the snapshot token, never a raw source ID

    def __post_init__(self) -> None:
        _sha("snapshot", self.snapshot)
        _ident("table", self.table)
        _text("record_id", self.record_id)

    def to_obj(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "table": self.table,
            "record_id": self.record_id,
        }

    @classmethod
    def from_obj(cls, obj: object) -> "RecordRef":
        o = _exact("RecordRef", obj, {"snapshot", "table", "record_id"})
        return cls(o["snapshot"], o["table"], o["record_id"])


@dataclass(frozen=True, slots=True)
class InvestigationRequest:
    """What an investigator is given. Carries no case ID and no class words."""

    request_id: str
    snapshot: str  # snapshot content digest
    primary_entity: RecordRef
    symptom: str
    cutoff: date
    policy_version: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        _ident("request_id", self.request_id)
        _sha("snapshot", self.snapshot)
        _type("primary_entity", self.primary_entity, RecordRef)
        if self.primary_entity.snapshot != self.snapshot:
            raise ValueError("primary_entity belongs to a different snapshot")
        _text("symptom", self.symptom)
        if type(self.cutoff) is not date:  # datetime is a date subclass
            raise TypeError("cutoff must be a date")
        _ident("policy_version", self.policy_version)

    def to_obj(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "snapshot": self.snapshot,
            "primary_entity": self.primary_entity.to_obj(),
            "symptom": self.symptom,
            "cutoff": self.cutoff.isoformat(),
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_obj(cls, obj: object) -> "InvestigationRequest":
        _check_version(obj)
        o = _exact(
            "InvestigationRequest",
            obj,
            {
                "schema_version",
                "request_id",
                "snapshot",
                "primary_entity",
                "symptom",
                "cutoff",
                "policy_version",
            },
        )
        return cls(
            request_id=o["request_id"],
            snapshot=o["snapshot"],
            primary_entity=RecordRef.from_obj(o["primary_entity"]),
            symptom=o["symptom"],
            cutoff=_iso_date("cutoff", o["cutoff"]),
            policy_version=o["policy_version"],
            schema_version=o["schema_version"],
        )

    def to_json(self) -> str:
        return _dumps(self.to_obj())

    @classmethod
    def from_json(cls, text: str) -> "InvestigationRequest":
        return cls.from_obj(_loads(text))


@dataclass(frozen=True, slots=True)
class AbsenceSearch:
    """A bounded search that found `observed_count` rows (evidence-contract
    principle 3). Absence is claimed by a count of 0, never by a missing ID."""

    snapshot: str
    table: str
    predicate: str  # the exact, re-runnable condition, scope included
    cutoff: date
    observed_count: int

    def __post_init__(self) -> None:
        _sha("snapshot", self.snapshot)
        _ident("table", self.table)
        _text("predicate", self.predicate)
        if type(self.cutoff) is not date:
            raise TypeError("cutoff must be a date")
        if type(self.observed_count) is not int or self.observed_count < 0:
            raise ValueError("observed_count must be an integer >= 0")

    def to_obj(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "table": self.table,
            "predicate": self.predicate,
            "cutoff": self.cutoff.isoformat(),
            "observed_count": self.observed_count,
        }

    @classmethod
    def from_obj(cls, obj: object) -> "AbsenceSearch":
        o = _exact(
            "AbsenceSearch",
            obj,
            {"snapshot", "table", "predicate", "cutoff", "observed_count"},
        )
        return cls(
            o["snapshot"],
            o["table"],
            o["predicate"],
            _iso_date("cutoff", o["cutoff"]),
            o["observed_count"],
        )


def _timestamp_day(name: str, v: str) -> date:
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        raise ValueError(f"{name} must be an ISO date or datetime, got {v!r}") from None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One cited thing: a record or an absence search (exactly one).

    `values` are the allowed column values as strings (amounts as "12.50 EUR");
    `timestamps` are the dates and times the record carries; `relationship`
    says how it was retrieved (for example "payment_ref").
    """

    evidence_id: str
    record: RecordRef | None
    search: AbsenceSearch | None
    relationship: str
    values: tuple[tuple[str, str], ...] = ()
    timestamps: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _ident("evidence_id", self.evidence_id)
        if (self.record is None) == (self.search is None):
            raise ValueError("exactly one of record and search is required")
        if self.record is not None:
            _type("record", self.record, RecordRef)
        if self.search is not None:
            _type("search", self.search, AbsenceSearch)
        _ident("relationship", self.relationship)
        _set(
            self,
            values=_pairs("values", self.values),
            timestamps=_pairs("timestamps", self.timestamps),
        )
        for k, v in self.timestamps:
            _timestamp_day(f"timestamp {k}", v)

    @property
    def snapshot(self) -> str:
        ref = self.record if self.record is not None else self.search
        assert ref is not None
        return ref.snapshot

    def to_obj(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "record": None if self.record is None else self.record.to_obj(),
            "search": None if self.search is None else self.search.to_obj(),
            "relationship": self.relationship,
            "values": dict(self.values),
            "timestamps": dict(self.timestamps),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "EvidenceItem":
        o = _exact(
            "EvidenceItem",
            obj,
            {"evidence_id", "record", "search", "relationship", "values", "timestamps"},
        )
        return cls(
            o["evidence_id"],
            None if o["record"] is None else RecordRef.from_obj(o["record"]),
            None if o["search"] is None else AbsenceSearch.from_obj(o["search"]),
            o["relationship"],
            tuple(_type("values", o["values"], dict).items()),
            tuple(_type("timestamps", o["timestamps"], dict).items()),
        )


@dataclass(frozen=True, slots=True)
class Operand:
    evidence_id: str  # the evidence item the amount was read from
    field: str  # the column of that item
    amount: Money

    def __post_init__(self) -> None:
        _ident("evidence_id", self.evidence_id)
        _ident("field", self.field)
        _type("amount", self.amount, Money)

    def to_obj(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "field": self.field,
            "amount": str(self.amount),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "Operand":
        o = _exact("Operand", obj, {"evidence_id", "field", "amount"})
        return cls(o["evidence_id"], o["field"], Money.from_str(o["amount"]))


# Contractual or descriptive dates may lie after the request cutoff (a due date is
# known in advance). Every other timestamp is an availability time and must not.
CONTRACTUAL_DATE_FIELDS = frozenset({"due_date"})

OPERATIONS = ("sum", "difference", "abs_difference")


def _compute(operation: str, amounts: list[Money]) -> Money:
    if operation == "sum":
        total = amounts[0]
        for a in amounts[1:]:
            total = total + a
        return total
    if operation == "difference":  # first minus all the others
        diff = amounts[0]
        for a in amounts[1:]:
            diff = diff - a
        return diff
    return abs(amounts[0] - amounts[1])


@dataclass(frozen=True, slots=True)
class Calculation:
    """An exact monetary result over operands that cite evidence.

    Validation recomputes the result from the operands, so a reported number
    that does not follow from them cannot be constructed. `tolerance` is the
    explicit, supported equality rule for the comparison made with the result
    (one minor unit, not a percentage); it is a declaration, not applied here.
    """

    calculation_id: str
    operation: str
    operands: tuple[Operand, ...]
    result: Money
    tolerance: Money | None = None

    def __post_init__(self) -> None:
        _ident("calculation_id", self.calculation_id)
        if self.operation not in OPERATIONS:
            raise ValueError(f"operation must be one of {OPERATIONS}")
        _tuple("operands", self.operands, Operand)
        need = {"sum": 1, "difference": 2, "abs_difference": 2}[self.operation]
        if len(self.operands) < need or (
            self.operation == "abs_difference" and len(self.operands) != 2
        ):
            raise ValueError(f"{self.operation} has the wrong number of operands")
        _type("result", self.result, Money)
        expected = _compute(self.operation, [o.amount for o in self.operands])
        if self.result != expected:  # raises CurrencyMismatchError on mixed currencies
            raise ValueError(f"result {self.result} does not equal {expected}")
        if self.tolerance is not None:
            _type("tolerance", self.tolerance, Money)
            if self.tolerance.currency != self.result.currency:
                raise ValueError("tolerance currency differs from result currency")
            if self.tolerance.amount < 0:
                raise ValueError("tolerance must not be negative")

    def to_obj(self) -> dict[str, Any]:
        return {
            "calculation_id": self.calculation_id,
            "operation": self.operation,
            "operands": [o.to_obj() for o in self.operands],
            "result": str(self.result),
            "tolerance": None if self.tolerance is None else str(self.tolerance),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "Calculation":
        o = _exact(
            "Calculation",
            obj,
            {"calculation_id", "operation", "operands", "result", "tolerance"},
        )
        return cls(
            o["calculation_id"],
            o["operation"],
            _each("operands", o["operands"], Operand.from_obj),
            Money.from_str(o["result"]),
            None if o["tolerance"] is None else Money.from_str(o["tolerance"]),
        )


@dataclass(frozen=True, slots=True)
class Finding:
    check_id: str
    state: FindingState
    evidence_ids: tuple[str, ...] = ()
    calculation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ident("check_id", self.check_id)
        _type("state", self.state, FindingState)
        for name, ids in (
            ("evidence_ids", self.evidence_ids),
            ("calculation_ids", self.calculation_ids),
        ):
            for i in _tuple(name, ids, str):
                _ident(name, i)
            _unique(name, ids)
        if self.state is not FindingState.UNKNOWN and not (
            self.evidence_ids or self.calculation_ids
        ):
            raise ValueError(f"{self.state.value} finding must cite evidence")

    def to_obj(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "state": self.state.value,
            "evidence_ids": list(self.evidence_ids),
            "calculation_ids": list(self.calculation_ids),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "Finding":
        o = _exact(
            "Finding", obj, {"check_id", "state", "evidence_ids", "calculation_ids"}
        )
        return cls(
            o["check_id"],
            FindingState(o["state"]),
            _strs("evidence_ids", o["evidence_ids"]),
            _strs("calculation_ids", o["calculation_ids"]),
        )


def _is_legitimate(category: str) -> bool:
    return category == LEGITIMATE or category.startswith(LEGITIMATE + "_")


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """Execution outcome (`disposition`) is separate from the conclusion
    (`category`). Only `answered` carries a category, so missing evidence
    (abstained, failed) can never read as `legitimate`; and `legitimate`
    itself needs findings that are all supported and nothing missing."""

    disposition: Disposition
    category: str | None
    findings: tuple[Finding, ...] = ()
    alternatives: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    reason: str | None = None  # why not answered, e.g. "unsupported_fx"

    def __post_init__(self) -> None:
        _type("disposition", self.disposition, Disposition)
        _tuple("findings", self.findings, Finding)
        _unique("findings", tuple(f.check_id for f in self.findings))
        for i in _tuple("alternatives", self.alternatives, str):
            _ident("alternative", i)
        for m in _tuple("missing_information", self.missing_information, str):
            _text("missing_information item", m)
        if self.disposition is Disposition.ANSWERED:
            if self.category is None:
                raise ValueError("an answered diagnosis needs a category")
            _ident("category", self.category)
            if self.reason is not None:
                raise ValueError("an answered diagnosis has no reason")
            if not any(f.state is FindingState.SUPPORTED for f in self.findings):
                raise ValueError("an answered diagnosis needs a supported finding")
            if self.category in self.alternatives:
                raise ValueError("category repeated in alternatives")
            if _is_legitimate(self.category) and (
                any(f.state is not FindingState.SUPPORTED for f in self.findings)
                or self.missing_information
            ):
                raise ValueError(
                    "legitimate needs every finding supported and nothing missing"
                )
        else:
            if self.category is not None:
                raise ValueError(
                    f"a {self.disposition.value} diagnosis has no category"
                )
            _text("reason", self.reason)

    def to_obj(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "category": self.category,
            "findings": [f.to_obj() for f in self.findings],
            "alternatives": list(self.alternatives),
            "missing_information": list(self.missing_information),
            "reason": self.reason,
        }

    @classmethod
    def from_obj(cls, obj: object) -> "Diagnosis":
        o = _exact(
            "Diagnosis",
            obj,
            {
                "disposition",
                "category",
                "findings",
                "alternatives",
                "missing_information",
                "reason",
            },
        )
        return cls(
            Disposition(o["disposition"]),
            o["category"],
            _each("findings", o["findings"], Finding.from_obj),
            _strs("alternatives", o["alternatives"]),
            _strs("missing_information", o["missing_information"]),
            o["reason"],
        )


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A proposed action for a person. Nothing here executes anything."""

    action_type: str
    supporting_findings: tuple[str, ...]  # check_ids of the diagnosis
    prerequisites: tuple[str, ...] = ()
    unresolved_assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ident("action_type", self.action_type)
        for i in _tuple("supporting_findings", self.supporting_findings, str):
            _ident("supporting_findings", i)
        if not self.supporting_findings:
            raise ValueError("a recommendation needs a supporting finding")
        for name, items in (
            ("prerequisites", self.prerequisites),
            ("unresolved_assumptions", self.unresolved_assumptions),
        ):
            for t in _tuple(name, items, str):
                _text(name, t)

    def to_obj(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "supporting_findings": list(self.supporting_findings),
            "prerequisites": list(self.prerequisites),
            "unresolved_assumptions": list(self.unresolved_assumptions),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "Recommendation":
        o = _exact(
            "Recommendation",
            obj,
            {
                "action_type",
                "supporting_findings",
                "prerequisites",
                "unresolved_assumptions",
            },
        )
        return cls(
            o["action_type"],
            _strs("supporting_findings", o["supporting_findings"]),
            _strs("prerequisites", o["prerequisites"]),
            _strs("unresolved_assumptions", o["unresolved_assumptions"]),
        )


def _check_operand(calculation_id: str, op: Operand, item: EvidenceItem) -> None:
    """The operand must be exactly the cited record's value (amount and currency)."""
    if item.record is None:
        raise ValueError(
            f"{calculation_id}: {op.evidence_id} is a search, not a record, "
            "so it cannot supply a money operand"
        )
    cited = dict(item.values).get(op.field)
    if cited is None:
        raise ValueError(
            f"{calculation_id}: {op.evidence_id} has no field {op.field!r}"
        )
    if Money.from_str(cited) != op.amount:
        raise ValueError(
            f"{calculation_id}: operand {op.amount} does not match "
            f"{op.evidence_id}.{op.field} = {cited}"
        )


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    """The whole answer: every reference inside it resolves inside it."""

    request: InvestigationRequest
    diagnosis: Diagnosis
    evidence: tuple[EvidenceItem, ...] = ()
    calculations: tuple[Calculation, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"unsupported schema_version {self.schema_version!r}"
            )
        _type("request", self.request, InvestigationRequest)
        _type("diagnosis", self.diagnosis, Diagnosis)
        _tuple("evidence", self.evidence, EvidenceItem)
        _tuple("calculations", self.calculations, Calculation)
        _tuple("recommendations", self.recommendations, Recommendation)
        for t in _tuple("limitations", self.limitations, str):
            _text("limitations item", t)
        _set(self, provenance=_pairs("provenance", self.provenance))

        evidence_ids = tuple(e.evidence_id for e in self.evidence)
        calc_ids = tuple(c.calculation_id for c in self.calculations)
        _unique("evidence ids", evidence_ids)
        _unique("calculation ids", calc_ids)
        for e in self.evidence:
            if e.snapshot != self.request.snapshot:
                raise ValueError(f"{e.evidence_id} is from a different snapshot")
            if e.search is not None and e.search.cutoff != self.request.cutoff:
                raise ValueError(f"{e.evidence_id} searched under another cutoff")
            for k, v in e.timestamps:
                if k in CONTRACTUAL_DATE_FIELDS:
                    continue
                if _timestamp_day(k, v) > self.request.cutoff:
                    raise ValueError(f"{e.evidence_id} {k} is after the cutoff")
        by_id = {e.evidence_id: e for e in self.evidence}
        for c in self.calculations:
            for op in c.operands:
                if op.evidence_id not in by_id:
                    raise ValueError(f"{c.calculation_id}: unknown {op.evidence_id}")
                _check_operand(c.calculation_id, op, by_id[op.evidence_id])
        for f in self.diagnosis.findings:
            for i in f.evidence_ids:
                if i not in evidence_ids:
                    raise ValueError(f"{f.check_id}: unknown evidence {i}")
            for i in f.calculation_ids:
                if i not in calc_ids:
                    raise ValueError(f"{f.check_id}: unknown calculation {i}")
        checks = {f.check_id for f in self.diagnosis.findings}
        for r in self.recommendations:
            for i in r.supporting_findings:
                if i not in checks:
                    raise ValueError(f"{r.action_type}: unknown finding {i}")

    def to_obj(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request": self.request.to_obj(),
            "diagnosis": self.diagnosis.to_obj(),
            "evidence": [e.to_obj() for e in self.evidence],
            "calculations": [c.to_obj() for c in self.calculations],
            "recommendations": [r.to_obj() for r in self.recommendations],
            "limitations": list(self.limitations),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_obj(cls, obj: object) -> "EvidencePacket":
        _check_version(obj)
        o = _exact(
            "EvidencePacket",
            obj,
            {
                "schema_version",
                "request",
                "diagnosis",
                "evidence",
                "calculations",
                "recommendations",
                "limitations",
                "provenance",
            },
        )
        return cls(
            request=InvestigationRequest.from_obj(o["request"]),
            diagnosis=Diagnosis.from_obj(o["diagnosis"]),
            evidence=_each("evidence", o["evidence"], EvidenceItem.from_obj),
            calculations=_each("calculations", o["calculations"], Calculation.from_obj),
            recommendations=_each(
                "recommendations", o["recommendations"], Recommendation.from_obj
            ),
            limitations=_strs("limitations", o["limitations"]),
            provenance=tuple(_type("provenance", o["provenance"], dict).items()),
            schema_version=o["schema_version"],
        )

    def to_json(self) -> str:
        return _dumps(self.to_obj())

    @classmethod
    def from_json(cls, text: str) -> "EvidencePacket":
        return cls.from_obj(_loads(text))
