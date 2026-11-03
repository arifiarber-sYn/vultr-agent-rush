"""Vultr Serverless Inference adapter (OpenAI-compatible, stdlib only).

The planner is the only component that needs a model. It talks to Vultr
Serverless Inference through the OpenAI-compatible /chat/completions endpoint
configured by three environment variables:

    VULTR_INFERENCE_BASE_URL   e.g. https://api.vultrinference.com/v1
    VULTR_INFERENCE_API_KEY    bearer token
    VULTR_INFERENCE_MODEL      e.g. a served chat model name

Honesty rule: if any variable is missing we never pretend a model answered.
The planner reports source="local-fallback" plus the exact reason, and the
fallback produces a deterministic plan instead of chat text.

The API key is never placed in logs, error messages, or repr output.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

ENV_BASE_URL = "VULTR_INFERENCE_BASE_URL"
ENV_API_KEY = "VULTR_INFERENCE_API_KEY"
ENV_MODEL = "VULTR_INFERENCE_MODEL"

DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 1_000_000


class InferenceError(RuntimeError):
    """A configured remote inference call failed (transport, HTTP, or shape)."""


@dataclass(frozen=True)
class InferenceConfig:
    """Remote inference settings; an empty field means 'not configured'."""

    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "InferenceConfig":
        source = os.environ if env is None else env
        return cls(
            base_url=(source.get(ENV_BASE_URL) or "").strip(),
            api_key=(source.get(ENV_API_KEY) or "").strip(),
            model=(source.get(ENV_MODEL) or "").strip(),
        )

    @property
    def missing(self) -> list[str]:
        pairs = ((ENV_BASE_URL, self.base_url), (ENV_API_KEY, self.api_key), (ENV_MODEL, self.model))
        return [name for name, value in pairs if not value]

    @property
    def configured(self) -> bool:
        return not self.missing

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def chat(
    config: InferenceConfig,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 900,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener=None,
) -> str:
    """POST an OpenAI-style chat request and return the assistant text.

    opener is injectable so tests run without any network access. Raises
    InferenceError for every failure mode; the message never contains the API
    key or request headers.
    """
    if not config.configured:
        raise InferenceError(f"inference not configured; missing {', '.join(config.missing)}")

    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        config.endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agent-rush/1.0",
        },
    )

    call = urllib.request.urlopen if opener is None else opener
    try:
        with call(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:  # 4xx/5xx
        raise InferenceError(f"inference HTTP {exc.code} from {config.endpoint}") from None
    except urllib.error.URLError:
        raise InferenceError(f"inference unreachable at {config.endpoint}") from None
    except OSError:
        raise InferenceError(f"inference transport error at {config.endpoint}") from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise InferenceError("inference response exceeded size cap")

    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
        message = parsed["choices"][0]["message"]
        text = message.get("content")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise InferenceError("inference response was not OpenAI-compatible") from None

    if not isinstance(text, str) or not text.strip():
        raise InferenceError("inference returned an empty assistant message")
    return text


def inference_status(env: dict[str, str] | None = None) -> dict:
    """Secret-free description of the inference wiring (safe for /health)."""
    config = InferenceConfig.from_env(env)
    return {
        "configured": config.configured,
        "mode": "vultr-serverless-inference" if config.configured else "local-fallback",
        "model": config.model or None,
        "endpoint": config.endpoint if config.base_url else None,
        "missing_env": config.missing,
    }
