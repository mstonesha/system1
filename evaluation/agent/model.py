"""Thin evaluation-only model interface.

Live calls use an OpenAI-compatible Chat Completions HTTP API.
No vendor is assumed: the operator supplies base URL, model name,
and API key. Tests use StubAnalysisModel and must not perform
network calls.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from evaluation.agent.prompt import ModelPrompt


API_KEY_ENV = "EVAL_AGENT_API_KEY"
BASE_URL_ENV = "EVAL_AGENT_BASE_URL"
MODEL_ENV = "EVAL_AGENT_MODEL"
TIMEOUT_ENV = "EVAL_AGENT_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 120
ERROR_BODY_LIMIT = 240


@dataclass(frozen=True)
class ModelRawResponse:
    text: str
    model_identifier: str


class AnalysisModel(Protocol):
    @property
    def identifier(self) -> str: ...

    def analyse(self, prompt: ModelPrompt) -> ModelRawResponse: ...


class StubAnalysisModel:
    """In-process stand-in. Used by tests; never calls a provider."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    @property
    def identifier(self) -> str:
        return "stub"

    def analyse(self, prompt: ModelPrompt) -> ModelRawResponse:
        return ModelRawResponse(
            text=self._response_text,
            model_identifier=self.identifier,
        )


class OpenAICompatibleModel:
    """POST /chat/completions against a configured HTTP endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def identifier(self) -> str:
        return self._model

    def analyse(self, prompt: ModelPrompt) -> ModelRawResponse:
        url = f"{self._base_url}/chat/completions"
        body = json.dumps(
            {
                "model": self._model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                format_provider_http_error(
                    exc.code,
                    _http_error_body(exc),
                    secret=self._api_key,
                )
            ) from None

        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Configured provider returned an unexpected payload."
            ) from exc
        if not isinstance(text, str):
            raise RuntimeError(
                "Configured provider returned a non-text message."
            )
        return ModelRawResponse(
            text=text,
            model_identifier=self.identifier,
        )


def load_configured_model() -> AnalysisModel | None:
    """Return a live model if all required env vars are set.

    Missing or blank variables mean no provider is configured.
    Empty strings are treated as unset.
    """
    api_key = _env(API_KEY_ENV)
    base_url = _env(BASE_URL_ENV)
    model = _env(MODEL_ENV)
    if api_key is None or base_url is None or model is None:
        return None
    timeout_raw = _env(TIMEOUT_ENV)
    timeout = (
        float(timeout_raw)
        if timeout_raw is not None
        else DEFAULT_TIMEOUT_SECONDS
    )
    return OpenAICompatibleModel(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout,
    )


def format_provider_http_error(
    status: int,
    body: bytes,
    *,
    secret: str = "",
) -> str:
    """Summarise a non-2xx provider body without leaking secrets."""
    text = _redact_secret(
        body.decode("utf-8", errors="replace"),
        secret,
    )
    error = _provider_error_object(text)
    if error is not None:
        message = _error_field(error, "message")
        err_type = _error_field(error, "type")
        code = _error_field(error, "code")
        param = _error_field(error, "param")
        if message is not None:
            message = _clip(_redact_secret(message, secret))
        return _compose_http_error(
            status,
            message=message,
            err_type=err_type,
            code=code,
            param=param,
        )
    snippet = _clip(text) if text.strip() else None
    if snippet:
        return f"Model HTTP {status}: {snippet}"
    return f"Model HTTP {status} from configured provider."


def _http_error_body(exc: urllib.error.HTTPError) -> bytes:
    if getattr(exc, "fp", None) is None:
        return b""
    try:
        return exc.read()
    except OSError:
        return b""


def _provider_error_object(text: str) -> dict | str | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, (dict, str)):
        return error
    return None


def _error_field(error: dict | str, name: str) -> str | None:
    if isinstance(error, str):
        if name != "message":
            return None
        stripped = error.strip()
        return stripped or None
    value = error.get(name)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _compose_http_error(
    status: int,
    *,
    message: str | None,
    err_type: str | None,
    code: str | None,
    param: str | None,
) -> str:
    lead = code or err_type
    result = f"Model HTTP {status}"
    if lead:
        result = f"{result}: {lead}"
    if message:
        quoted = json.dumps(message, ensure_ascii=False)
        if lead:
            result = f"{result} — {quoted}"
        else:
            result = f"{result}: {quoted}"
    extras: list[str] = []
    if err_type and err_type != lead:
        extras.append(f"type: {err_type}")
    if param:
        extras.append(f"param: {param}")
    if extras:
        result = f"{result} ({'; '.join(extras)})"
    return result


def _redact_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


def _clip(text: str, limit: int = ERROR_BODY_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def missing_provider_message() -> str:
    return (
        "No evaluation model is configured.\n"
        "Live runs require:\n"
        f"  {API_KEY_ENV}\n"
        f"  {BASE_URL_ENV}\n"
        f"  {MODEL_ENV}\n"
        "The harness uses a thin OpenAI-compatible Chat Completions "
        "client. No vendor is assumed. Use --dry-run to inspect "
        "evidence and prompt without an API call."
    )


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped
