"""API tests: in-process, no Docker and no network."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import ENV_API_KEY, ENV_BASE_URL, ENV_MODEL

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clear_inference_env(monkeypatch):
    for name in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL):
        monkeypatch.delenv(name, raising=False)


def test_health_reports_local_fallback_mode() -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["inference"]["configured"] is False
    assert body["inference"]["mode"] == "local-fallback"


def test_plan_endpoint_returns_steps() -> None:
    response = client.post("/plan", json={"task": "sum the numbers 1..10"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local-fallback"
    assert len(body["steps"]) >= 2
    assert body["steps"][-1]["kind"] == "verify"


def test_plan_endpoint_rejects_empty_task() -> None:
    assert client.post("/plan", json={"task": ""}).status_code == 422


def test_submit_and_fetch_job(monkeypatch) -> None:
    def fake_run(code: str, job_id: str | None = None) -> dict:
        return {
            "id": job_id,
            "ok": True,
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "error": None,
            "verified": True,
            "containment": {"network": "none"},
        }

    monkeypatch.setattr(main, "run_in_sandbox", fake_run)
    body = client.post("/tasks", json={"code": "print('ok')"}).json()
    assert body["ok"] is True and body["verified"] is True
    assert client.get("/tasks/" + body["id"]).json()["id"] == body["id"]
    assert client.get("/tasks/doesnotexist").status_code == 404
    listed = client.get("/tasks").json()
    assert listed["count"] >= 1
