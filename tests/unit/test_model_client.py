import ast
import builtins
import dataclasses
import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reconcile.models.budget import SpendLedger
from reconcile.models.client import (
    AnthropicClient,
    ModelError,
    ModelRequest,
    ModelResponse,
    ReplayClient,
    complete_with_retry,
    make_client,
    request_key,
)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def make_req(**kw: Any) -> ModelRequest:
    base: dict[str, Any] = {
        "model": "claude-haiku-5-5",
        "system": "sys",
        "user": "usr",
        "schema": SCHEMA,
        "max_output_tokens": 500,
        "effort": "low",
    }
    base.update(kw)
    return ModelRequest(**base)


def make_resp(**kw: Any) -> ModelResponse:
    base: dict[str, Any] = {
        "text": '{"a": "x"}',
        "stop_reason": "end_turn",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 0,
        "model": "claude-haiku-5-5",
        "request_id": "req_1",
        "latency_s": 0.5,
    }
    base.update(kw)
    return ModelResponse(**base)


def test_request_key_stable_and_sensitive() -> None:
    req = make_req()
    assert request_key(req) == request_key(make_req())
    changes: dict[str, Any] = {
        "model": "claude-sonnet-5-5",
        "system": "other",
        "user": "other",
        "schema": {"type": "object"},
        "max_output_tokens": 501,
        "effort": "high",
    }
    assert set(changes) == {f.name for f in dataclasses.fields(ModelRequest)}
    keys = {request_key(make_req(**{k: v})) for k, v in changes.items()}
    assert len(keys | {request_key(req)}) == len(changes) + 1


def test_request_key_ignores_schema_key_order() -> None:
    a = make_req(schema={"x": 1, "y": 2})
    b = make_req(schema={"y": 2, "x": 1})
    assert request_key(a) == request_key(b)


def test_replay_roundtrip_and_miss(tmp_path: Path) -> None:
    path = tmp_path / "replay.jsonl"
    req, resp = make_req(), make_resp()
    path.write_text(
        json.dumps({"key": request_key(req), "response": dataclasses.asdict(resp)})
        + "\n"
    )
    client = ReplayClient(path)
    assert client.complete(req) == resp
    with pytest.raises(ModelError) as exc:
        client.complete(make_req(user="different"))
    assert exc.value.reason == "replay_miss"


def test_make_client_modes(tmp_path: Path) -> None:
    assert isinstance(make_client("replay", tmp_path / "r.jsonl"), ReplayClient)
    assert isinstance(make_client("live", None), AnthropicClient)
    assert isinstance(make_client("record", tmp_path / "r.jsonl"), AnthropicClient)
    with pytest.raises(ValueError):
        make_client("replay", None)
    with pytest.raises(ValueError):
        make_client("bogus", None)


# --- fake SDK -------------------------------------------------------------


class _SdkError(Exception):
    pass


class BadRequestError(_SdkError): ...


class AuthenticationError(_SdkError): ...


class PermissionDeniedError(_SdkError): ...


class NotFoundError(_SdkError): ...


class RateLimitError(_SdkError): ...


class APIConnectionError(_SdkError): ...


class APITimeoutError(APIConnectionError): ...


class APIStatusError(_SdkError):
    def __init__(self, status_code: int) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code


def fake_sdk(outcomes: list[Any], calls: list[dict[str, Any]]) -> Any:
    """Fake `anthropic` module whose messages.create pops scripted outcomes."""

    class Messages:
        def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out

    class Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"init": kwargs})
            self.messages = Messages()

    return SimpleNamespace(
        Anthropic=Anthropic,
        BadRequestError=BadRequestError,
        AuthenticationError=AuthenticationError,
        PermissionDeniedError=PermissionDeniedError,
        NotFoundError=NotFoundError,
        RateLimitError=RateLimitError,
        APIStatusError=APIStatusError,
        APIConnectionError=APIConnectionError,
        APITimeoutError=APITimeoutError,
    )


def sdk_response(stop: str = "end_turn", text: str = '{"a": "x"}') -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=7,
            cache_creation_input_tokens=0,
        ),
        _request_id="req_abc",
    )


def test_live_request_construction(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    record = tmp_path / "rec.jsonl"
    client = AnthropicClient(record_to=record, sdk=fake_sdk([sdk_response()], calls))
    req = make_req()
    resp = client.complete(req)

    assert calls[0] == {"init": {"timeout": 60.0, "max_retries": 0}}
    sent = calls[1]
    assert sent == {
        "model": "claude-haiku-5-5",
        "max_tokens": 500,
        "system": "sys",
        "messages": [{"role": "user", "content": "usr"}],
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    }
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in sent
    assert resp.text == '{"a": "x"}'
    assert (resp.input_tokens, resp.output_tokens, resp.cache_read_tokens) == (
        1000,
        200,
        7,
    )
    assert resp.request_id == "req_abc"
    # The recording replays to the same response.
    assert ReplayClient(record).complete(req) == resp


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (BadRequestError(), "bad_request"),
        (NotFoundError(), "bad_request"),
        (AuthenticationError(), "auth"),
        (PermissionDeniedError(), "auth"),
        (RateLimitError(), "rate_limited"),
        (APITimeoutError(), "timeout"),
        (APIConnectionError(), "connection"),
        (APIStatusError(500), "server_error"),
        (APIStatusError(529), "server_error"),
        (APIStatusError(418), "bad_request"),
    ],
)
def test_live_error_mapping(exc: Exception, reason: str, tmp_path: Path) -> None:
    record = tmp_path / "rec.jsonl"
    client = AnthropicClient(record_to=record, sdk=fake_sdk([exc], []))
    with pytest.raises(ModelError) as err:
        client.complete(make_req())
    assert err.value.reason == reason
    assert not record.exists()  # errors are never recorded


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_stop_reasons_become_errors_and_are_not_recorded(
    stop: str, tmp_path: Path
) -> None:
    record = tmp_path / "rec.jsonl"
    client = AnthropicClient(record_to=record, sdk=fake_sdk([sdk_response(stop)], []))
    with pytest.raises(ModelError) as err:
        client.complete(make_req())
    assert err.value.reason == stop
    assert (err.value.input_tokens, err.value.output_tokens) == (1000, 200)
    assert not record.exists()


# --- retry policy ---------------------------------------------------------


class ScriptedClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, ModelResponse)
        return out


def run(
    outcomes: list[Any], tmp_path: Path, cap: str = "25"
) -> tuple[ScriptedClient, SpendLedger]:
    client = ScriptedClient(outcomes)
    ledger = SpendLedger(tmp_path / "l.json", Decimal(cap))
    return client, ledger


@pytest.mark.parametrize(
    "reason", ["rate_limited", "server_error", "timeout", "connection"]
)
def test_transient_retried_once(reason: str, tmp_path: Path) -> None:
    client, ledger = run([ModelError(reason), make_resp()], tmp_path)
    resp = complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert resp.text is not None
    assert client.calls == 2
    assert ledger.total_usd() == Decimal("0.0002")


def test_second_transient_surfaces(tmp_path: Path) -> None:
    client, ledger = run([ModelError("timeout"), ModelError("rate_limited")], tmp_path)
    with pytest.raises(ModelError) as err:
        complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert err.value.reason == "rate_limited"
    assert client.calls == 2


@pytest.mark.parametrize("reason", ["bad_request", "auth", "replay_miss"])
def test_non_transient_not_retried(reason: str, tmp_path: Path) -> None:
    client, ledger = run([ModelError(reason), make_resp()], tmp_path)
    with pytest.raises(ModelError):
        complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert client.calls == 1
    assert not (tmp_path / "l.json").exists()  # unbilled, nothing recorded


def test_refusal_not_retried_but_billed(tmp_path: Path) -> None:
    err = ModelError(
        "refusal", model="claude-haiku-5-5", input_tokens=1000, output_tokens=200
    )
    client, ledger = run([err], tmp_path)
    with pytest.raises(ModelError):
        complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert client.calls == 1
    assert ledger.total_usd() == Decimal("0.0002")


def test_budget_refusal_happens_before_the_call(tmp_path: Path) -> None:
    client, ledger = run([make_resp()], tmp_path, cap="0.000001")
    with pytest.raises(ModelError) as err:
        complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert err.value.reason == "budget"
    assert client.calls == 0


def test_budget_checked_before_the_retry(tmp_path: Path) -> None:
    # Estimate for the request is 2 chars-ish input plus 500 output tokens:
    # 500*0.50/1e6 + tiny = just over 0.00025. Cap allows one attempt only.
    client, ledger = run(
        [
            ModelError(
                "timeout",
            ),
            make_resp(),
        ],
        tmp_path,
        cap="0.0003",
    )
    ledger.record("seed", "claude-haiku-5-5", 0, 0, "ok")  # zero-cost seed
    # First attempt: timeout recorded at zero cost, so a retry still fits.
    complete_with_retry(client, make_req(), ledger)  # type: ignore[arg-type]
    assert client.calls == 2
    # Now spent 0.0002; next case's estimate (~0.00025) exceeds the 0.0003 cap.
    client2 = ScriptedClient([make_resp()])
    with pytest.raises(ModelError) as err:
        complete_with_retry(client2, make_req(), ledger)  # type: ignore[arg-type]
    assert err.value.reason == "budget"
    assert client2.calls == 0


# --- import boundaries ----------------------------------------------------


def test_models_import_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    for mod in [m for m in sys.modules if m.startswith("reconcile.models")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", blocked)
    importlib.import_module("reconcile.models")
    client_mod = importlib.import_module("reconcile.models.client")
    importlib.import_module("reconcile.models.budget")
    with pytest.raises(ImportError):
        client_mod.AnthropicClient().complete(make_req())


def test_domain_has_no_provider_or_models_imports() -> None:
    domain = Path(__file__).resolve().parents[2] / "src" / "reconcile" / "domain"
    for py in domain.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(("anthropic", "reconcile.models")), (py, n)
