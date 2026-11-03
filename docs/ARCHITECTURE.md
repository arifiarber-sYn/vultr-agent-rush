# Architecture — plan, dispatch, verify

## 1. Shape of the system

```
                       ┌──────────────────────────────────────────┐
   HTTP client ───────►│  FastAPI control layer (app/main.py)     │
   (judge, curl, UI)   │  /health  /plan  /tasks  /tasks/{id}     │
                       └───────┬───────────────────────┬──────────┘
                               │                       │
                     plan only │                       │ execute only
                               ▼                       ▼
                 ┌───────────────────────┐   ┌──────────────────────────┐
                 │ Planner               │   │ Sandbox runner           │
                 │ (app/planner.py)      │   │ (app/sandbox.py)         │
                 │                       │   │                          │
                 │ Vultr Serverless      │   │ builds a docker run      │
                 │ Inference adapter     │   │ command, enforces a      │
                 │ (app/llm.py), OpenAI  │   │ wall clock, captures     │
                 │ compatible, stdlib    │   │ stdout/stderr/exit code  │
                 │                       │   │                          │
                 │ no model -> explicit  │   │ no docker -> explicit    │
                 │ local fallback        │   │ sandbox_unavailable      │
                 └───────────┬───────────┘   └────────────┬─────────────┘
                             │                            │
                             ▼                            ▼
                   Plan{source, model,          Job{ok, exit_code, stdout,
                        steps[]}                 stderr, verified,
                                                 containment{}}
```

Two endpoints, one boundary. Planning never executes; execution never plans.
A model can influence *what to do next* only to a human reading `/plan` — it
cannot reach the Docker call by itself.

## 2. Components

| Component | File | Responsibility | Failure behaviour |
|---|---|---|---|
| Control layer | `app/main.py` | HTTP surface, job registry, response shape | 404 for unknown job, 422 for invalid input |
| Planner | `app/planner.py` | task -> ordered steps, JSON validation | falls back locally, records why in `note` |
| Inference adapter | `app/llm.py` | OpenAI-compatible POST, timeouts, size caps | raises `InferenceError`, never leaks the key |
| Sandbox | `app/sandbox.py` | command construction + `subprocess.run` + timeout kill | structured failure record, never a 500 |

The container flags live in exactly one place (`build_command`), and the tests
assert them there. There is no second code path that could run code "quickly".

## 3. Sequence: a contained run

1. Client sends `POST /tasks {"code": "print(6*7)"}`.
2. `build_command` produces the docker argv (no shell interpolation, no `docker exec`).
3. `subprocess.run(argv, timeout=20)` starts the container.
4. Inside: `python -I -c <code>` as uid 65534, cwd `/work` (16 MiB tmpfs,
   `mode=1777,noexec,nosuid`), root filesystem read-only.
5. Process exits; the job record stores exit code, last 8 KB stdout, last 4 KB
   stderr, `verified` (exit code 0), and the `containment` block.
6. On `TimeoutExpired`: `docker kill run-<id>`, then a failure record.

## 4. Security decisions

| Decision | Flag / mechanism | Why | Verified by |
|---|---|---|---|
| No network | `--network none` | exfiltration, C2, scanning, package install all die here | `test_network_is_none` |
| Read-only root | `--read-only` | no tampering with binaries, no persistence | `test_runs_as_non_root_readonly` |
| Writable scratch only | `--tmpfs /work:rw,size=16m,mode=1777,noexec,nosuid` | bounded, non-executable workspace that dies with the container; mode=1777 so uid 65534 can actually write there | `test_workdir_is_writable_by_sandbox_user` |
| Drop capabilities | `--cap-drop ALL` | removes mount/chroot/raw-socket paths | `test_runs_as_non_root_readonly` |
| No privilege escalation | `--security-opt no-new-privileges` | setuid binaries cannot gain rights | command-construction test |
| Non-root | `--user 65534:65534` | no ownership of host-mapped files, no uid-0 semantics | `test_runs_as_non_root_readonly` |
| Process cap | `--pids-limit 64` | fork bombs die with `BlockingIOError` | `test_resource_caps_present` |
| Memory cap | `--memory 256m` | OOM-kill instead of host pressure | `test_resource_caps_present` |
| CPU cap | `--cpus 0.5` | noisy-neighbour and miner economics | `test_resource_caps_present` |
| Wall clock | `subprocess.run(timeout=20)` + `docker kill` | infinite loops cannot hold a worker | runner branch |
| No shell | argv list, `python -I -c` | no shell metacharacter injection into the launcher | `test_isolation_flag_on_interpreter` |
| Never privileged | `FORBIDDEN_FLAGS` assertion | a future edit cannot quietly add `--privileged` | `test_privileged_never_used` |
| Key hygiene | `field(repr=False)`, no headers in errors | a log line never becomes a credential leak | `test_repr_never_contains_api_key` |

`python -I` is deliberate too: isolated mode ignores `PYTHONPATH` and user site
packages, so the payload cannot import a host-mounted helper even if one
appeared in the image.

## 5. Threat model

| Attack | Result | Evidence returned |
|---|---|---|
| Read files outside the box | no host bind mounts; root is read-only | `FileNotFoundError` / empty |
| Write to the host | tmpfs only, container removed with `--rm` | exit 0, artifact gone |
| Exfiltrate via HTTP/DNS | no interface | `OSError: Network is unreachable` |
| Fork bomb | 64 pids | `BlockingIOError` |
| Miner / infinite loop | 0.5 CPU, 20 s, then kill | `ok: false`, `timeout after 20s` |
| Memory bomb | 256 MiB limit | `MemoryError` / OOM kill, exit != 0 |
| Escape via capabilities | all dropped, non-root, no-new-privileges | permission denied |

Out of scope, stated openly: a Docker daemon or kernel compromise, and the
control layer itself being malicious. Blast radius zero for *untrusted payloads*,
not for a compromised host.

## 6. Verification model

"Verified" means machine-checkable, not "looks fine":

- `exit_code == 0` -> `verified: true`; anything else -> false.
- The `containment` block is emitted from the same constants used to build the
  command, so the record cannot drift from the argv.
- `FORBIDDEN_FLAGS` is asserted at run time, not only in tests.
- Every remote-model path has a parse check; a plan that is not JSON, has fewer
  than two usable steps, or lacks a title is rejected, with fallback recorded.
- The final step of any plan is forced to `kind: "verify"`, so the display can
  never end on an unverified execution.

## 7. Failure modes

| Failure | Behaviour |
|---|---|
| Docker missing | `error: sandbox_unavailable`, HTTP 200 with `ok: false` |
| Image pull fails | Docker stderr in `stderr`, exit != 0, `verified: false` |
| Timeout | container killed, `error: timeout after 20s` |
| Inference unset | `source: local-fallback` + missing variable names |
| Inference 401/5xx | `source: local-fallback` + "remote model unavailable: ..." |
| Model returns prose | `source: local-fallback` + "unusable plan" |
| Model returns 2 MB | rejected at 1 MB cap |

## 8. Extension points

- `SANDBOX_IMAGE` / limits as settings (currently module constants, tested).
- A durable `JOBS` store behind the same record shape.
- A `seccomp` profile and user-namespace remap for a harder boundary.
- Artifact export (bounded, explicit) instead of stdout-only.
