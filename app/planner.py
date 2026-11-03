"""Planner: turn a task sentence into an ordered plan of steps.

Two sources, one shape:

* "vultr"          - a served model on Vultr Serverless Inference answered and
                     its JSON was validated.
* "local-fallback" - no model was configured/reachable/parseable; a
                     deterministic rule-based planner produced the steps and
                     the reason is recorded in Plan.note.

Either way the output is structured steps, never conversational prose: the
dispatch layer consumes Plan.steps and nothing else.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import InferenceConfig, InferenceError, chat, provider_label

STEP_KINDS = ("inspect", "execute", "verify")
MAX_TASK_CHARS = 4000
MAX_STEPS = 8

SYSTEM_PROMPT = """You are the planning engine of a sandboxed agent runtime.
Given one task, reply with JSON only, no prose, of this exact shape:
{"steps": [{"title": "short imperative", "kind": "inspect|execute|verify", "detail": "one sentence"}]}

Rules:
- 3 to 6 steps, in execution order.
- Every step must be something the sandboxed runner can do with code it writes itself.
- The last step MUST have kind "verify".
- Never ask the user questions. Never answer the task; plan it.
"""


@dataclass(frozen=True)
class PlanStep:
    index: int
    title: str
    kind: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"index": self.index, "title": self.title, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class Plan:
    task: str
    steps: list[PlanStep]
    source: str
    model: str | None = None
    note: str | None = field(default=None)

    @property
    def uses_remote_model(self) -> bool:
        return self.source != "local-fallback"

    def as_dict(self) -> dict:
        return {
            "task": self.task,
            "source": self.source,
            "model": self.model,
            "note": self.note,
            "steps": [step.as_dict() for step in self.steps],
        }


def clip(text: str, limit: int = 280) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "..."


def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of model output (code fences tolerated)."""
    cleaned = text.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(cleaned[start : end + 1])


def parse_steps(payload: dict) -> list[PlanStep]:
    """Validate the model JSON into PlanStep objects; raise ValueError if unusable."""
    raw_steps = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("plan JSON has no steps list")

    steps: list[PlanStep] = []
    for raw in raw_steps[:MAX_STEPS]:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in STEP_KINDS:
            kind = "execute"
        detail = raw.get("detail")
        steps.append(
            PlanStep(
                index=len(steps) + 1,
                title=clip(title, 120),
                kind=kind,
                detail=clip(detail, 300) if isinstance(detail, str) else "",
            )
        )
    if len(steps) < 2:
        raise ValueError("plan JSON yielded fewer than two usable steps")
    if steps[-1].kind != "verify":
        steps[-1] = PlanStep(index=steps[-1].index, title=steps[-1].title,
                             kind="verify", detail=steps[-1].detail)
    return steps


def _intent_steps(task: str) -> list[tuple[str, str, str]]:
    """Deterministic, task-shaped step templates used when no model answers."""
    lower = task.lower()
    steps: list[tuple[str, str, str]] = [
        ("Inspect the request and fix the inputs", "inspect", clip(task)),
    ]
    if any(word in lower for word in ("file", "csv", "json", "read", "parse", "skedar")):
        steps.append(
            ("Read the input inside the sandbox", "execute",
             "Copy only the declared input into the tmpfs workdir; nothing is mounted from the host.")
        )
    if any(word in lower for word in ("http", "url", "api", "download", "rrjet")):
        steps.append(
            ("Fetch remote data from the control plane", "inspect",
             "The sandbox has no network: fetch in the control layer, hand the payload in as data.")
        )
    steps.append(
        ("Run the task as an isolated python step", "execute",
         "docker run --network none --read-only --cap-drop ALL, python -I, 20 s wall clock.")
    )
    steps.append(
        ("Check the produced output", "verify",
         "Assert exit code 0, inspect stdout/stderr, and confirm the recorded containment flags.")
    )
    return steps


def fallback_plan(task: str, reason: str) -> Plan:
    """Local plan with an explicit, non-hidden reason."""
    steps = [
        PlanStep(index=i, title=title, kind=kind, detail=detail)
        for i, (title, kind, detail) in enumerate(_intent_steps(task), start=1)
    ]
    return Plan(task=task, steps=steps, source="local-fallback", note=reason)


def plan_task(task: str, *, env: dict[str, str] | None = None, opener=None,
              temperature: float = 0.0) -> Plan:
    """Plan task with Vultr inference when configured, else fall back locally."""
    task = clip(task, MAX_TASK_CHARS)
    if not task:
        raise ValueError("task must not be empty")

    config = InferenceConfig.from_env(env)
    if not config.configured:
        return fallback_plan(task, f"no remote model configured (missing {', '.join(config.missing)})")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    try:
        text = chat(config, messages, temperature=temperature, opener=opener)
    except InferenceError as exc:
        return fallback_plan(task, f"remote model unavailable: {exc}")

    try:
        steps = parse_steps(extract_json_object(text))
    except (ValueError, json.JSONDecodeError) as exc:
        return fallback_plan(task, f"remote model returned an unusable plan: {exc}")

    label = provider_label(config.base_url)
    return Plan(
        task=task,
        steps=steps,
        source="vultr" if label == "vultr-serverless-inference" else label,
        model=config.model,
    )
