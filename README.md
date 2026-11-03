# Agent Rush — Blast Radius Zero

A containment-first agent runtime. An agent is only useful if it can *do* things;
it is only safe to run if doing things cannot reach your host, your network, or
your data. This service plans a task with a hosted model, executes the concrete
step inside a locked-down Docker container, and returns a verifiable record of
what ran and what contained it.

**One sentence:** every execution is a fresh, networkless, read-only, non-root,
resource-capped container, and the API hands back the containment flags next to
the exit code so a judge can check both.

```
POST /plan  {"task": "read data.csv and report the row count"}
   -> {"source": "local-fallback", "steps": [ ... 3 steps ... ]}

POST /tasks {"code": "print(6 * 7)"}
   -> {"ok": true, "exit_code": 0, "stdout": "42\n", "verified": true,
       "containment": {"network": "none", "read_only": true, "cap_drop": "ALL", ...}}
```

## The containment moment

The "containment moment" is the second when hostile or buggy code tries to leave
the box and the box says no — and the API still returns a clean, structured
result instead of a crash or a compromise. Reproduce it in one minute:

```bash
bash scripts/containment_demo.sh http://127.0.0.1:8000
```

| Payload sent to POST /tasks | What the sandbox does | What you get back |
|---|---|---|
| `print(6 * 7)` | runs normally | `exit_code 0`, `stdout "42"` |
| `socket.create_connection(("1.1.1.1", 80))` | no interface at all | `exit_code 1`, `Network is unreachable` |
| `open("/etc/passwd", "w")` | read-only root filesystem | `exit_code 1`, `Read-only file system` |
| `open("/work/notes.txt", "w")` | allowed, tmpfs only | `exit_code 0`, file dies with the container |
| `os.fork()` in a loop | pids limit 64 | `exit_code 1`, `BlockingIOError` |
| `while True: pass` | 20 s wall clock, then `docker kill` | `ok false`, `timeout after 20s` |

Every response carries the same `containment` block, so nothing about the
isolation is implied — it is reported per job.

## How it works

```
                 POST /plan
   task text ─────────────────► planner.plan_task
                                     │
                                     ├── Vultr Serverless Inference (OpenAI-compatible)
                                     │        returns JSON steps
                                     └── local deterministic planner (if no model)
                                              │
                                              ▼
                                    Plan { source, model, steps[] }
                                    (steps, never chat prose)

                 POST /tasks
   code text ─────────────────► sandbox.build_command
                                     │  --network none --read-only --cap-drop ALL
                                     │  --pids-limit 64 --memory 256m --cpus 0.5
                                     │  --user 65534:65534 --tmpfs /work
                                     ▼
                              docker run --rm  ──►  python -I -c <code>
                                     │
                                     ▼
                    { ok, exit_code, stdout, stderr,
                      verified, containment{...} }
```

Full design notes, threat model and failure modes: **docs/ARCHITECTURE.md**.

## Layout

```
app/main.py          FastAPI control layer: /health /plan /tasks
app/planner.py       plan -> steps (remote model or explicit local fallback)
app/llm.py           Vultr Serverless Inference adapter (stdlib, OpenAI-compatible)
app/sandbox.py       docker command builder + runner (containment flags live here)
tests/               35 pytest tests, no Docker and no network required
scripts/             containment demo
docs/                architecture and security decisions
```

## Run locally

Requirements: Python 3.12+, Docker (only for `POST /tasks`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the generated OpenAPI UI.

Without Docker the control layer still starts; `POST /tasks` returns
`sandbox_unavailable: docker CLI not found on PATH` instead of pretending it ran.

## Tests

```bash
python -m pytest -q
# 35 passed
```

The suite is intentionally offline: the inference adapter is tested through an
injected `opener` (a fake `urlopen`), the Docker runner through a fake
`subprocess.run`, and the API through `TestClient`. No container is built and no
socket is opened.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + secret-free inference status |
| POST | `/plan` | `{"task": "..."}` -> ordered steps (`inspect` / `execute` / `verify`) |
| POST | `/tasks` | `{"code": "..."}` -> run in the sandbox, returns job record |
| GET | `/tasks` | recent jobs (newest first) |
| GET | `/tasks/{id}` | one job record |

The job record is the artifact: `exit_code`, `stdout`, `stderr`,
`verified` (exit code 0), and the `containment` block actually used for that run.

## Vultr Serverless Inference

The planner speaks the OpenAI-compatible `/chat/completions` shape. Copy
`.env.example` to `.env` and set:

```bash
VULTR_INFERENCE_BASE_URL=https://api.vultrinference.com/v1
VULTR_INFERENCE_API_KEY=...   # never commit this
VULTR_INFERENCE_MODEL=...     # a chat model served on your account
```

Then the planner sends a system prompt that demands JSON only, validates the
result, and forces the final step to be `verify`. If any variable is missing,
the request fails, the model answers prose instead of JSON, or the model returns
an unusable plan, the API does **not** hide it:

```json
{"source": "local-fallback",
 "model": null,
 "note": "remote model unavailable: inference HTTP 401 from https://.../chat/completions",
 "steps": [...]}
```

That honesty is deliberate: a judge can tell exactly which brain produced the
plan, and the demo never depends on a network being up.

## Deploy on Vultr

**Recommended (full plan → dispatch → verify loop).** The control layer spawns
`docker run`, so it must run where a Docker daemon is reachable:

```bash
# Ubuntu 24.04 Cloud Compute instance, Docker installed
git clone <your-repo> && cd agent-rush
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the three Vultr values (optional)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open port 8000 only for the demo window (`ufw allow 8000/tcp`), put it behind
a TLS proxy if it will stay up, and pre-pull the sandbox image
(`docker pull python:3.12-slim`) so the first request is fast.

**API-only container.** `docker build -t agent-rush .` + `docker run -p 8000:8000`
serves `/health` and `/plan` perfectly, but that image has no Docker CLI, so
`/tasks` answers `sandbox_unavailable`. Mounting the host socket into a container
would defeat the blast-radius story — run the dispatcher on the host instead.

## Known limits

- Jobs live in process memory; a restart forgets them. Fine for a demo, wrong for
  a product (a real version needs a durable store and a queue).
- No authentication or multi-tenancy. Put it behind a proxy and a token before
  exposing it beyond a demo.
- The Docker daemon is trusted. A kernel/runC escape is outside this layer's
  blast radius, which is why the flags, the non-root user and `--cap-drop ALL`
  matter: they remove the easy paths.
- The planner is advisory. `/plan` returns steps; `/tasks` executes code sent by
  the caller. The two are intentionally separate endpoints, so a bad plan cannot
  silently become a bad execution.
- Sandbox I/O is stdout/stderr only (8 KB / 4 KB tails). Binary artifacts would
  need an explicit export step.
- First run pulls `python:3.12-slim`; without network the run fails closed with
  the Docker error in `stderr`.

## License

MIT © 2026 Arber Arifi — see LICENSE.
