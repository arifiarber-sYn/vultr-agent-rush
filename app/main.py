"""FastAPI control layer: submit a task, dispatch it to the sandbox, return verifiable output."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .sandbox import run_in_sandbox

app = FastAPI(title="Agent Rush control layer")
JOBS: dict[str, dict] = {}


class Task(BaseModel):
    code: str = Field(min_length=1, max_length=20000)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "jobs": len(JOBS)}


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


@app.get("/tasks/{job_id}")
def get_task(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    return JOBS[job_id]
