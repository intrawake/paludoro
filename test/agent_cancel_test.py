"""Tests for agent stop/cancel functionality."""

from unittest.mock import AsyncMock, patch, MagicMock

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from paludoro.server import app
from paludoro.state import PaludoroSession
from paludoro.agent_pipeline import AgentPipeline

client = TestClient(app)


# ── Stop endpoint tests ────────────────────────────────────────────────


def test_stop_nonexistent_agent_returns_404(monkeypatch):
    """Stop request for a non-running agent returns 404."""
    session = PaludoroSession()
    monkeypatch.setattr("paludoro.server.global_session", session)
    res = client.post("/api/agents/nonexistent/stop")
    assert res.status_code == 404
    assert "not running" in res.json()["error"]


def test_stop_running_agent_closes_http_client(monkeypatch):
    """Stop request for a running agent closes its httpx client."""
    session = PaludoroSession()
    session.running_agents["test_agent"] = 1234567890.0
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.aclose = AsyncMock()
    session.agent_http_clients["test_agent"] = mock_client

    monkeypatch.setattr("paludoro.server.global_session", session)
    res = client.post("/api/agents/test_agent/stop")
    assert res.status_code == 200

    # Verify client was closed
    mock_client.aclose.assert_awaited_once()
    # Verify client removed from session
    assert "test_agent" not in session.agent_http_clients
    # Verify cancel signal set
    assert session.is_cancelled("test_agent")


def test_stop_sets_cancel_signal(monkeypatch):
    """Stop sets the cancel signal even without an HTTP client."""
    session = PaludoroSession()
    session.running_agents["test_agent"] = 1234567890.0
    monkeypatch.setattr("paludoro.server.global_session", session)
    res = client.post("/api/agents/test_agent/stop")
    assert res.status_code == 200
    assert session.is_cancelled("test_agent")


# ── Session state tests ────────────────────────────────────────────────


def test_cancel_signal_lifecycle():
    """Cancel signal is set, checked, and cleared properly."""
    session = PaludoroSession()
    assert not session.is_cancelled("agent")
    session.request_cancel("agent")
    assert session.is_cancelled("agent")
    session.clear_cancel("agent")
    assert not session.is_cancelled("agent")


def test_running_agents_timestamps():
    """Running agents are stored with started_at timestamps."""
    session = PaludoroSession()
    session.running_agents["agent"] = 1720000000.0
    assert session.running_agents["agent"] == 1720000000.0
    assert "agent" not in session.agent_cancel_signals


# ── Pipeline cancel flow tests ─────────────────────────────────────────


@pytest.fixture
def cancel_config():
    return {
        "agent_dict": {
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["/dev/stdin"],
                "exposes": ["dialogue_line.txt"],
                "prompt_as": {"filepath": "main_prompt.md"},
            },
        }
    }


@pytest.fixture
def cancel_test_preset(tmp_path):
    """Create a temporary main_prompt.md so the agent finds a prompt."""
    preset_dir = tmp_path / "preset"
    preset_dir.mkdir()
    (preset_dir / "main_prompt.md").write_text("Test prompt")
    (preset_dir / "config.sxpb").write_text("()")
    return preset_dir


@pytest.mark.asyncio
async def test_cancelled_agent_returns_early(monkeypatch):
    """Agent that is cancelled before the API call returns early with no artifacts."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://mock")
    session = PaludoroSession()
    session.artifacts["/dev/stdin"] = "hello"
    session.request_cancel("main")

    config = {
        "agent_dict": {
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["/dev/stdin"],
                "exposes": ["dialogue_line.txt"],
            },
        }
    }

    pipeline = AgentPipeline(config, session)
    result = await pipeline.run_agent("main")
    assert result == []


@pytest.mark.asyncio
async def test_agent_client_stored_and_cleaned_up(monkeypatch):
    """Agent stores its httpx client on the session and cleans it up."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://mock")
    session = PaludoroSession()
    session.artifacts["/dev/stdin"] = "hello"

    config = {
        "agent_dict": {
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["/dev/stdin"],
                "exposes": ["dialogue_line.txt"],
                "prompt_as": {"remote": "/dev/stdin"},
            },
        }
    }

    # Patch call_api to return empty (simulating cancelled/failed)
    with patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = ""

        pipeline = AgentPipeline(config, session)
        await pipeline.run_agent("main")

    # Client should be cleaned up
    assert "main" not in session.agent_http_clients
    assert "main" not in session.running_agents
    assert not session.is_cancelled("main")


@pytest.mark.asyncio
async def test_client_stored_on_session_during_run(monkeypatch):
    """Agent stores its httpx client on session.agent_http_clients during execution."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://mock")
    session = PaludoroSession()
    session.artifacts["/dev/stdin"] = "hello"

    stored_client = None

    async def capture_client(*args, **kwargs):
        nonlocal stored_client
        stored_client = kwargs.get("httpx_client")
        return ""

    config = {
        "agent_dict": {
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["/dev/stdin"],
                "exposes": ["dialogue_line.txt"],
                "prompt_as": {"remote": "/dev/stdin"},
            },
        }
    }

    with patch("paludoro.agent_pipeline.call_api", side_effect=capture_client):
        pipeline = AgentPipeline(config, session)
        await pipeline.run_agent("main")

    # Client was stored during execution
    assert stored_client is not None
    # Client is cleaned up after execution
    assert "main" not in session.agent_http_clients
    assert stored_client.is_closed
