"""Unit tests for containment flags. Run: python3 tests/test_sandbox.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sandbox import build_command, FORBIDDEN_FLAGS


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


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print("—" * 30)
    print("të gjitha kaluan" if failures == 0 else f"{failures} dështuan")
    sys.exit(1 if failures else 0)
