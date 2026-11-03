"""Tests for the Vultr Serverless Inference adapter. No network: opener is faked."""
import json
import urllib.error

import pytest

from app.llm import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    InferenceConfig,
    InferenceError,
    chat,
    inference_status,
)

SECRET = "vultr-test-secret-key"
BASE = "https://api.vultrinference.com/v1"
MODEL = "test-chat-model"

FULL_ENV = {ENV_BASE_URL: BASE, ENV_API_KEY: SECRET, ENV_MODEL: MODEL}


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._payload
        return self._payload[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class Recorder:
    """Stands in for urllib.request.urlopen; records requests, returns or raises."""

    def __init__(self, payload: bytes = b"", error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, request, timeout=None):
        self.calls.append({"request": request, "timeout": timeout, "body": request.data})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


def openai_payload(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


def test_config_reads_env() -> None:
    config = InferenceConfig.from_env(FULL_ENV)
    assert config.configured
    assert config.missing == []
    assert config.endpoint == BASE + "/chat/completions"


def test_partial_config_reports_only_missing_names() -> None:
    config = InferenceConfig.from_env({ENV_BASE_URL: BASE})
    assert not config.configured
    assert config.missing == [ENV_API_KEY, ENV_MODEL]


def test_repr_never_contains_api_key() -> None:
    config = InferenceConfig.from_env(FULL_ENV)
    assert SECRET not in repr(config)
    assert SECRET not in str(config)


def test_status_is_secret_free() -> None:
    status = inference_status(FULL_ENV)
    assert status["configured"] is True
    assert status["model"] == MODEL
    assert SECRET not in json.dumps(status)


def test_chat_sends_openai_compatible_request() -> None:
    recorder = Recorder(openai_payload('{"steps": []}'))
    config = InferenceConfig.from_env(FULL_ENV)
    text = chat(config, [{"role": "user", "content": "hello"}], opener=recorder)

    assert text == '{"steps": []}'
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["request"].full_url == BASE + "/chat/completions"
    assert call["request"].get_method() == "POST"
    assert call["request"].headers["Authorization"] == "Bearer " + SECRET
    body = json.loads(call["body"].decode())
    assert body["model"] == MODEL
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["temperature"] == 0.0


def test_chat_refuses_unconfigured_config() -> None:
    with pytest.raises(InferenceError) as excinfo:
        chat(InferenceConfig(), [{"role": "user", "content": "x"}])
    assert ENV_MODEL in str(excinfo.value)


def test_http_error_becomes_inference_error_without_secret() -> None:
    error = urllib.error.HTTPError(BASE, 401, "unauthorized", {}, None)
    recorder = Recorder(error=error)
    with pytest.raises(InferenceError) as excinfo:
        chat(InferenceConfig.from_env(FULL_ENV), [{"role": "user", "content": "x"}], opener=recorder)
    message = str(excinfo.value)
    assert "401" in message
    assert SECRET not in message


def test_unreachable_host_becomes_inference_error() -> None:
    recorder = Recorder(error=urllib.error.URLError("no route to host"))
    with pytest.raises(InferenceError) as excinfo:
        chat(InferenceConfig.from_env(FULL_ENV), [{"role": "user", "content": "x"}], opener=recorder)
    assert "unreachable" in str(excinfo.value)


def test_non_openai_body_is_rejected() -> None:
    recorder = Recorder(b'{"result": "nope"}')
    with pytest.raises(InferenceError):
        chat(InferenceConfig.from_env(FULL_ENV), [{"role": "user", "content": "x"}], opener=recorder)


def test_blank_assistant_message_is_rejected() -> None:
    recorder = Recorder(openai_payload("   "))
    with pytest.raises(InferenceError):
        chat(InferenceConfig.from_env(FULL_ENV), [{"role": "user", "content": "x"}], opener=recorder)
