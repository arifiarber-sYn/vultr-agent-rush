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
SANDBOX_USER = "65534:65534"

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
        "--user", SANDBOX_USER,
        "--workdir", "/work",
        IMAGE,
        "python", "-I", "-c", code,
    ]


def containment_record() -> dict:
    """The flags we claim, in machine-readable form, for the job record."""
    return {
        "network": "none",
        "memory": MEMORY,
        "cpus": CPUS,
        "pids": PIDS,
        "read_only": True,
        "cap_drop": "ALL",
        "no_new_privileges": True,
        "user": SANDBOX_USER,
        "timeout_s": TIMEOUT_S,
        "image": IMAGE,
    }


def _failure(job_id: str | None, *, error: str, exit_code: int | None, stderr: str) -> dict:
    return {
        "id": job_id,
        "ok": False,
        "exit_code": exit_code,
        "stdout": "",
        "stderr": stderr,
        "error": error,
        "verified": False,
        "containment": containment_record(),
    }


def run_in_sandbox(code: str, job_id: str | None = None) -> dict:
    """Execute code in the sandbox; return structured, verifiable result."""
    cmd = build_command(code, job_id)
    assert not any(f in cmd for f in FORBIDDEN_FLAGS), "privileged flag must never appear"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return _failure(
            job_id,
            error="sandbox_unavailable: docker CLI not found on PATH",
            exit_code=None,
            stderr="the control layer needs a Docker host (see README: Deploy)",
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", f"run-{job_id or 'unknown'}"], capture_output=True)
        return _failure(
            job_id,
            error=f"timeout after {TIMEOUT_S}s",
            exit_code=None,
            stderr=f"timeout after {TIMEOUT_S}s",
        )

    return {
        "id": job_id,
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stdout": p.stdout[-8000:],
        "stderr": p.stderr[-4000:],
        "error": None,
        "verified": p.returncode == 0,
        "containment": containment_record(),
    }
