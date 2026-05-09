from fastapi.testclient import TestClient
from paludoro.server import app
from paludoro.api import clear_llm_request_log, _log_request

client = TestClient(app)


def test_api_artifacts():
    response = client.get("/api/artifacts")
    assert response.status_code == 200
    data = response.json()
    assert "artifacts" in data
    assert "version_counts" in data
    assert isinstance(data["artifacts"], dict)


def test_api_llm_requests_empty():
    clear_llm_request_log()
    response = client.get("/api/llm-requests")
    assert response.status_code == 200
    data = response.json()
    assert "requests" in data
    assert isinstance(data["requests"], list)
    assert len(data["requests"]) == 0


def test_api_llm_requests_with_entries():
    clear_llm_request_log()
    _log_request(
        model="test-model",
        agent_name="transcript",
        messages=[{"role": "user", "content": "hello"}],
        response="hi there",
        error=None,
        duration=1.5,
    )
    _log_request(
        model="test-model",
        agent_name="main",
        messages=[{"role": "user", "content": "prompt"}],
        response=None,
        error="HTTP 500: oops",
        duration=2.0,
    )
    response = client.get("/api/llm-requests")
    assert response.status_code == 200
    data = response.json()
    assert len(data["requests"]) == 2
    assert data["requests"][0]["agent"] == "transcript"
    assert data["requests"][1]["error"] == "HTTP 500: oops"
