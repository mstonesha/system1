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
                "temperature": 0,
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
                f"Model HTTP {exc.code} from configured provider."
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
