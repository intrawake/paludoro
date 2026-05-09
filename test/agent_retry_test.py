from unittest.mock import patch, AsyncMock
import asyncio
from paludoro.state import PaludoroSession
from paludoro.agent_pipeline import AgentPipeline


def test_agent_invalid_write_retry():
    async def run_test():
        config = {
            "agent_dict": {
                "test_agent": {
                    "generate_as": {"text": {"model": {"api_url": "mock"}}},
                    "prompt_as": {"remote": "input.sxpb"},
                    "remotes": ["input.sxpb"],
                    "exposes": ["allowed.sxpb"],
                }
            }
        }
        session = PaludoroSession()
        session.save_artifact("input.sxpb", "(dummy)")
        pipeline = AgentPipeline(config, session)

        # We will mock the API to return a bad write on attempt 1, and a good write on attempt 2
        responses = [
            "I will write to a bad artifact!\n\n>bad.sxpb\n(bad)\n\nAnd a good one\n\n>allowed.sxpb\n(good)\n",
            "Oops, my bad. Let me fix that.\n\n>allowed.sxpb\n(good 2)\n",
        ]

        async def mock_call_api(*args, **kwargs):
            return responses.pop(0)

        with patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ):
            changed = await pipeline.run_agent("test_agent")

        assert changed == ["allowed.sxpb"]
        assert session.artifacts.get("allowed.sxpb") == "(good 2)"
        assert "bad.sxpb" not in session.artifacts

    asyncio.run(run_test())
