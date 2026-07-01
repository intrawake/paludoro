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

        # allowed.sxpb was accepted on attempt 1; attempt 2 must not overwrite it
        assert changed == ["allowed.sxpb"]
        assert session.artifacts.get("allowed.sxpb") == "(good)"
        assert "bad.sxpb" not in session.artifacts

    asyncio.run(run_test())


def test_invalid_artifact_silently_ignored():
    """When the model writes to an artifact not in exposes, silently ignore it."""
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

    # Model writes to bad.sxpb (invalid) and allowed.sxpb (valid)
    response = (
        "I will write to a bad artifact!"
        "\n\n>bad.sxpb\n(bad)"
        "\n\nAnd a good one"
        "\n\n>allowed.sxpb\n(good)\n"
    )

    async def run():
        mock_api = AsyncMock(return_value=response)
        with patch("paludoro.agent_pipeline.call_api", new=mock_api):
            changed = await pipeline.run_agent("test_agent")

        # Only the valid artifact is accepted; bad.sxpb is silently ignored.
        assert changed == ["allowed.sxpb"]
        assert mock_api.call_count == 1
        assert session.artifacts["allowed.sxpb"] == "(good)"
        assert "bad.sxpb" not in session.artifacts

    asyncio.run(run())


def test_extra_closing_paren_stripped():
    """When an artifact ends with \n)\n), strip the last \n) and retry parse."""
    config = {
        "agent_dict": {
            "test_agent": {
                "generate_as": {"text": {"model": {"api_url": "mock"}}},
                "prompt_as": {"remote": "input.sxpb"},
                "remotes": ["input.sxpb"],
                "exposes": ["answer.sxpb"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("input.sxpb", "(dummy)")
    pipeline = AgentPipeline(config, session)

    # Nested SxPB with an extra closing paren line — model hallucination.
    response = 'Here you go!\n\n>answer.sxpb\n(answer\n  "hello world"\n)\n)\n'

    async def run():
        mock_api = AsyncMock(return_value=response)
        with patch("paludoro.agent_pipeline.call_api", new=mock_api):
            changed = await pipeline.run_agent("test_agent")

        # Should have been accepted without a retry.
        assert changed == ["answer.sxpb"]
        assert mock_api.call_count == 1
        stored = session.artifacts["answer.sxpb"]
        assert "hello world" in stored
        # Trailing \n)\n) stripped — ends with just one closing paren line.
        assert stored.endswith("\n)")

    asyncio.run(run())
