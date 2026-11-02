"""Sandbox runner: builds the docker command and executes a task in isolation.

Kept dependency-free (stdlib only) so the containment rules can be unit-tested
without Docker, a daemon, or a network.
"""
from __future__ import annotations

import subprocess
import uuid

IMAGE = "python:3.12-slim"
TIMEOUT_S = 20
MEMORY = "256m"
CPUS = "0.5"
PIDS = "64"

FORBIDDEN_FLAGS = ("--privileged",)


def build_command(code: str, job_id: str | None = None) -> list[str]:
    """Build a docker run command that executes *code* under hard containment."""
    job_id = job_id or uuid.uuid4().hex[:12]
    return [
        "docker", "run", "--rm",
        "--name", f"run-{job_id}",
        "--network", "none",
        "--memory", MEMORY,
        "--cpus", CPUS,
        "--pids-limit", PIDS,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/work:rw,size=16m,noexec,nosuid",
        "--user", "65534:65534",
        "--workdir", "/work",
        IMAGE,
        "python", "-I", "-c", code,
    ]


def run_in_sandbox(code: str, job_id: str | None = None) -> dict:
    """Execute code in the sandbox; return structured, verifiable result."""
    cmd = build_command(code, job_id)
    assert not any(f in cmd for f in FORBIDDEN_FLAGS), "privileged flag must never appear"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
        return {
            "ok": p.returncode == 0,
            "exit_code": p.returncode,
            "stdout": p.stdout[-8000:],
            "stderr": p.stderr[-4000:],
            "containment": {
                "network": "none", "memory": MEMORY, "cpus": CPUS,
                "pids": PIDS, "read_only": True, "cap_drop": "ALL",
            },
        }
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", f"run-{job_id or 'unknown'}"], capture_output=True)
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": f"timeout after {TIMEOUT_S}s", "containment": {"network": "none"}}
