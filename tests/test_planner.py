"""Planner tests. Every remote path is exercised with a fake opener (no network)."""
import json

import pytest

from app.llm import ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
from app.planner import STEP_KINDS, extract_json_object, parse_steps, plan_task

SECRET = "planner-secret-key"
FULL_ENV = {
    ENV_BASE_URL: "https://api.vultrinference.com/v1",
    ENV_API_KEY: SECRET,
    ENV_MODEL: "test-chat-model",
}

FENCE = chr(96) * 3


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        return self._payload if size is None or size < 0 else self._payload[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def model_reply(content: str):
    payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    def opener(request, timeout=None):
        return FakeResponse(payload)

    return opener


GOOD_PLAN = {
    "steps": [
        {"title": "Parse the request", "kind": "inspect", "detail": "Read the task and list inputs."},
        {"title": "Run the contained script", "kind": "execute", "detail": "python -I inside the sandbox."},
        {"title": "Assert the output", "kind": "verify", "detail": "Compare stdout with the expected value."},
    ]
}


def test_without_env_plan_is_explicit_local_fallback() -> None:
    plan = plan_task("sum a list of numbers", env={})
    assert plan.source == "local-fallback"
    assert plan.uses_remote_model is False
    assert plan.note is not None and ENV_MODEL in plan.note
    assert len(plan.steps) >= 2
    assert all(step.kind in STEP_KINDS for step in plan.steps)
    assert plan.steps[-1].kind == "verify"
    assert [step.index for step in plan.steps] == list(range(1, len(plan.steps) + 1))


def test_fallback_returns_steps_not_chat_prose() -> None:
    plan = plan_task("read data.csv and report the row count", env={})
    titles = " ".join(step.title for step in plan.steps).lower()
    assert "csv" in plan.task.lower()
    assert all(step.title and step.detail for step in plan.steps)
    assert "i cannot" not in titles and "sure" not in titles


def test_remote_model_plan_is_used_when_configured() -> None:
    chatty = "Sure, here is the plan:\n\n" + FENCE + "json\n" + json.dumps(GOOD_PLAN) + "\n" + FENCE
    plan = plan_task("compute the mean", env=FULL_ENV, opener=model_reply(chatty))
    assert plan.source == "vultr"
    assert plan.model == "test-chat-model"
    assert plan.note is None
    assert [step.title for step in plan.steps] == [s["title"] for s in GOOD_PLAN["steps"]]
    assert plan.steps[-1].kind == "verify"


def test_remote_prose_falls_back_with_reason() -> None:
    plan = plan_task("compute the mean", env=FULL_ENV, opener=model_reply("I would start by reading the file."))
    assert plan.source == "local-fallback"
    assert plan.note is not None and "unusable plan" in plan.note


def test_remote_transport_error_falls_back_with_reason() -> None:
    def broken(request, timeout=None):
        raise OSError("connection refused")

    plan = plan_task("compute the mean", env=FULL_ENV, opener=broken)
    assert plan.source == "local-fallback"
    assert plan.note is not None and "unavailable" in plan.note


def test_remote_plan_never_leaks_key_into_plan() -> None:
    plan = plan_task("compute the mean", env=FULL_ENV, opener=model_reply(json.dumps(GOOD_PLAN)))
    assert SECRET not in json.dumps(plan.as_dict())


def test_parse_steps_coerces_unknown_kind_and_forces_final_verify() -> None:
    steps = parse_steps({"steps": [
        {"title": "Do a thing", "kind": "teleport"},
        {"title": "Finish", "kind": "execute"},
    ]})
    assert steps[0].kind == "execute"
    assert steps[-1].kind == "verify"
    assert [step.index for step in steps] == [1, 2]


def test_parse_steps_rejects_empty_plan() -> None:
    with pytest.raises(ValueError):
        parse_steps({"steps": []})


def test_extract_json_object_ignores_surrounding_text() -> None:
    payload = extract_json_object('prefix {"steps": [{"title": "x"}]} suffix')
    assert payload == {"steps": [{"title": "x"}]}


def test_plan_as_dict_shape() -> None:
    record = plan_task("count words in notes.txt", env={}).as_dict()
    assert set(record) == {"task", "source", "model", "note", "steps"}
    assert all(set(step) == {"index", "title", "kind", "detail"} for step in record["steps"])


def test_empty_task_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_task("   ", env={})
