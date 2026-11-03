"""FastAPI control layer: plan a task, dispatch it to the sandbox, return verified output."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .llm import inference_status
from .planner import plan_task
from .sandbox import run_in_sandbox

app = FastAPI(title="Agent Rush control layer", version="1.0.0")
JOBS: dict[str, dict] = {}


class Task(BaseModel):
    code: str = Field(min_length=1, max_length=20000)


class PlanRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)


@app.get("/health")
def health() -> dict:
    """Liveness plus a secret-free view of how planning is wired."""
    return {"ok": True, "jobs": len(JOBS), "inference": inference_status()}


@app.post("/plan")
def plan(request: PlanRequest) -> dict:
    """Plan step of plan -> dispatch -> verify. Returns steps, never chat prose."""
    try:
        return plan_task(request.task).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.post("/tasks")
def submit(task: Task) -> dict:
    job_id = uuid.uuid4().hex[:12]
    started = time.time()
    result = run_in_sandbox(task.code, job_id)
    JOBS[job_id] = {
        "id": job_id,
        "started_at": started,
        "duration_s": round(time.time() - started, 3),
        "code": task.code,
        **result,
    }
    return JOBS[job_id]


@app.get("/tasks")
def list_tasks(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    recent = sorted(JOBS.values(), key=lambda job: job["started_at"], reverse=True)
    return {"count": len(JOBS), "jobs": recent[:limit]}


@app.get("/tasks/{job_id}")
def get_task(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    return JOBS[job_id]
