"""Unit tests for containment flags and runner failure branches.

Prefer pytest: python -m pytest -q
The __main__ block still works for a dependency-free run.
"""
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sandbox
from app.sandbox import FORBIDDEN_FLAGS, build_command, containment_record, run_in_sandbox


def test_network_is_none() -> None:
    cmd = build_command("print(1)")
    assert cmd[cmd.index("--network") + 1] == "none", "sandbox must have no network"


def test_resource_caps_present() -> None:
    cmd = build_command("print(1)")
    for flag in ("--memory", "--cpus", "--pids-limit"):
        assert flag in cmd, f"missing {flag}"
    assert cmd[cmd.index("--memory") + 1] == "256m"


def test_privileged_never_used() -> None:
    cmd = build_command("print(1)")
    for bad in FORBIDDEN_FLAGS:
        assert bad not in cmd


def test_runs_as_non_root_readonly() -> None:
    cmd = build_command("print(1)")
    assert cmd[cmd.index("--user") + 1] == "65534:65534"
    assert "--read-only" in cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"


def test_isolation_flag_on_interpreter() -> None:
    cmd = build_command("print(1)")
    assert "-I" in cmd, "python must run isolated (-I): no user site, no PYTHONPATH"


def test_containment_record_matches_command() -> None:
    record = containment_record()
    cmd = build_command("print(1)")
    assert record["network"] == cmd[cmd.index("--network") + 1]
    assert record["memory"] == cmd[cmd.index("--memory") + 1]
    assert record["cpus"] == cmd[cmd.index("--cpus") + 1]
    assert record["user"] == cmd[cmd.index("--user") + 1]
    assert record["read_only"] is True and record["no_new_privileges"] is True


def test_docker_missing_is_a_structured_failure(monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(sandbox.subprocess, "run", missing)
    result = run_in_sandbox("print(1)", "job-missing")

    assert result["ok"] is False
    assert result["verified"] is False
    assert result["exit_code"] is None
    assert "sandbox_unavailable" in result["error"]
    assert result["containment"]["network"] == "none"


def test_timeout_kills_the_container(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "kill"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, sandbox.TIMEOUT_S)

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = run_in_sandbox("while True: pass", "job-slow")

    assert result["ok"] is False
    assert result["verified"] is False
    assert "timeout" in result["error"]
    assert calls[-1] == ["docker", "kill", "run-job-slow"]


def test_success_record_is_verified(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "42\n", "")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = run_in_sandbox("print(42)", "job-ok")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "42"
    assert result["id"] == "job-ok"


if __name__ == "__main__":  # pragma: no cover - manual fallback runner
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if inspect.signature(fn).parameters:
                print(f"SKIP {name} (needs pytest fixtures)")
                continue
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("-" * 30)
    print("all passed" if failures == 0 else f"{failures} failed")
    sys.exit(1 if failures else 0)
