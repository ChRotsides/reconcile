"""Model client: replay (offline), live and record modes.

The provider SDK is imported only inside `AnthropicClient`, so nothing else
needs it. Errors are never cached or recorded as answers.
"""

import hashlib
import importlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from reconcile.models.budget import SpendLedger

TRANSIENT_REASONS = frozenset({"rate_limited", "server_error", "timeout", "connection"})
MAX_ATTEMPTS = 2  # one call plus one transient retry (protocol [budgets.model])


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    user: str
    schema: Mapping[str, Any]
    max_output_tokens: int
    effort: str  # "low" | "medium" | "high"


@dataclass(frozen=True)
class ModelResponse:
    text: str | None
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    model: str
    request_id: str | None
    latency_s: float


class ModelError(Exception):
    """`reason`: rate_limited, server_error, timeout, connection, bad_request,
    auth, refusal, max_tokens, replay_miss or budget. Token counts are set when
    the provider billed the failed call (refusal, max_tokens)."""

    def __init__(
        self,
        reason: str,
        message: str = "",
        *,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def request_key(req: ModelRequest) -> str:
    canonical = json.dumps(asdict(req), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReplayClient:
    """Serves recorded responses from a JSONL file of {"key", "response"}."""

    def __init__(self, path: Path) -> None:
        self._responses: dict[str, ModelResponse] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._responses[row["key"]] = ModelResponse(**row["response"])

    def complete(self, req: ModelRequest) -> ModelResponse:
        key = request_key(req)
        if key not in self._responses:
            raise ModelError("replay_miss", key)
        return self._responses[key]


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write on an O_APPEND descriptor keeps each line whole.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


class AnthropicClient:
    """Live client. Credentials come from the environment, never from code.

    `sdk` is injectable so tests can pass a fake module; by default the real
    `anthropic` package is imported on first use.
    """

    def __init__(self, record_to: Path | None = None, sdk: Any = None) -> None:
        self._record_to = record_to
        self._sdk = sdk
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if self._sdk is None:
                self._sdk = importlib.import_module("anthropic")
            # max_retries=0: the per-case retry policy lives in
            # complete_with_retry so hidden SDK retries cannot add billed calls.
            self._client = self._sdk.Anthropic(timeout=60.0, max_retries=0)
        return self._client

    def _map_error(self, exc: Exception, model: str) -> ModelError:
        sdk = self._sdk
        if isinstance(exc, sdk.BadRequestError | sdk.NotFoundError):
            reason = "bad_request"
        elif isinstance(exc, sdk.AuthenticationError | sdk.PermissionDeniedError):
            reason = "auth"
        elif isinstance(exc, sdk.RateLimitError):
            reason = "rate_limited"
        elif isinstance(exc, sdk.APITimeoutError):  # subclass of APIConnectionError
            reason = "timeout"
        elif isinstance(exc, sdk.APIConnectionError):
            reason = "connection"
        elif isinstance(exc, sdk.APIStatusError):
            reason = "server_error" if exc.status_code >= 500 else "bad_request"
        else:
            raise exc
        return ModelError(reason, type(exc).__name__, model=model)

    def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        start = time.monotonic()
        try:
            response = client.messages.create(
                model=req.model,
                max_tokens=req.max_output_tokens,
                system=req.system,
                messages=[{"role": "user", "content": req.user}],
                output_config={
                    "effort": req.effort,
                    "format": {"type": "json_schema", "schema": dict(req.schema)},
                },
            )
        except Exception as exc:
            raise self._map_error(exc, req.model) from exc
        latency = time.monotonic() - start

        usage = response.usage
        in_tok = int(usage.input_tokens)
        out_tok = int(usage.output_tokens)
        stop = str(response.stop_reason)
        if stop in ("refusal", "max_tokens"):
            raise ModelError(
                stop, model=req.model, input_tokens=in_tok, output_tokens=out_tok
            )
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ModelError(
                "bad_request",
                "no text block",
                model=req.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        result = ModelResponse(
            text=text,
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            model=req.model,
            request_id=getattr(response, "_request_id", None),
            latency_s=latency,
        )
        if self._record_to is not None:
            line = json.dumps({"key": request_key(req), "response": asdict(result)})
            _append_line(self._record_to, line)
        return result


def make_client(mode: str, replay_path: Path | None) -> ReplayClient | AnthropicClient:
    if mode == "replay":
        if replay_path is None:
            raise ValueError("replay mode needs a replay path")
        return ReplayClient(replay_path)
    if mode == "live":
        return AnthropicClient()
    if mode == "record":
        if replay_path is None:
            raise ValueError("record mode needs a replay path to write")
        return AnthropicClient(record_to=replay_path)
    raise ValueError(f"unknown model client mode: {mode!r}")


def complete_with_retry(
    client: ReplayClient | AnthropicClient,
    req: ModelRequest,
    ledger: "SpendLedger",
) -> ModelResponse:
    """One call plus at most one retry on a transient error, under the cap."""
    from reconcile.models.budget import estimate_usd

    key = request_key(req)
    chars = len(req.system) + len(req.user) + len(json.dumps(req.schema))
    estimate = estimate_usd(req.model, chars, req.max_output_tokens)
    for attempt in range(MAX_ATTEMPTS):
        ledger.check(estimate)
        try:
            response = client.complete(req)
        except ModelError as err:
            if err.reason in ("refusal", "max_tokens", *TRANSIENT_REASONS):
                # Billed or possibly billed: count it. Unbilled transients
                # carry zero tokens, so they record zero cost.
                ledger.record(
                    key, req.model, err.input_tokens, err.output_tokens, err.reason
                )
            if err.reason in TRANSIENT_REASONS and attempt + 1 < MAX_ATTEMPTS:
                continue
            raise
        ledger.record(
            key, req.model, response.input_tokens, response.output_tokens, "ok"
        )
        return response
    raise AssertionError("unreachable")  # pragma: no cover
