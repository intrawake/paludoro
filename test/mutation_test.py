from paludoro.agent_pipeline import AgentPipeline
from paludoro.state import PaludoroSession
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_agent_config_not_mutated():
    session = PaludoroSession()
    config = {
        "agent_dict": {
            "test_agent": {
                "generate_as": {
                    "text": {"model": {"name": "test", "api_url": "http://mock-api"}}
                },
                "prompt_as": {"instruction": "Hello"},
                "exposes": ["a.txt"],
                "expose_by_artipath": [{"b.txt": {"visible": False}}],
            }
        }
    }

    with patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = (
            "```text > a.txt\ncontent\n```\n```text > b.txt\ncontent\n```"
        )
        pipeline = AgentPipeline(config, session)

        await pipeline.run_agent("test_agent")
        assert config["agent_dict"]["test_agent"]["exposes"] == ["a.txt"]

        await pipeline.run_agent("test_agent")
        assert config["agent_dict"]["test_agent"]["exposes"] == ["a.txt"]
