# Agent Rush — sandboxed execution control layer

Containment-first agent runtime: a web app that plans a task, dispatches it to an
isolated Docker environment, and returns verifiable output as an artifact.

## Why it exists
An agent that only chats is a demo. This one executes real work (code, files) with
every action contained: no network, no host filesystem, capped memory/CPU/PIDs,
wall-clock timeout, and a captured artifact for every run.

## Layout
- `app/main.py` — FastAPI control layer (submit task, poll job, fetch artifacts)
- `app/sandbox.py` — pure command builder + runner for the sandbox container
- `tests/test_sandbox.py` — unit tests for containment flags (no Docker needed)
- `Dockerfile` — image for the control layer

## Containment rules (enforced in code, asserted in tests)
--network none · --memory 256m · --cpus 0.5 · --pids-limit 64 ·
--read-only root with tmpfs workdir · --cap-drop ALL · --security-opt no-new-privileges ·
non-root user · timeout kill.

## Run
```bash
pip install fastapi uvicorn
uvicorn app.main:app --reload
```

## Test
```bash
python3 tests/test_sandbox.py
```
