from unittest.mock import patch, AsyncMock
import asyncio
from paludoro.state import PaludoroSession
from paludoro.agent_pipeline import AgentPipeline


def _capture_retry_message(pipeline, session, first_response):
    """Helper: run agent with a bad first response, capture the retry user message.
    Returns the retry message content sent to the model.
    """
    retry_msg = None

    async def mock_call_api(*args, **kwargs):
        nonlocal retry_msg
        # Inspect the last message in the payload (the retry instruction)
        messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
        for m in messages:
            if m.get("role") == "user" and "Failed output artifacts:" in m.get(
                "content", ""
            ):
                retry_msg = m["content"]
        return first_response

    async def run():
        nonlocal retry_msg
        with patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ):
            await pipeline.run_agent("test_agent")
        return retry_msg

    return asyncio.run(run())


def _assert_retry_format(retry_msg: str):
    """Assert the retry message follows the expected format."""
    lines = retry_msg.split("\n")

    # Must start with Failed output artifacts:
    assert lines[0] == "Failed output artifacts:", (
        f"Expected 'Failed output artifacts:' as first line, got: {lines[0]}"
    )

    # Collect sections
    section_starts = [i for i, line in enumerate(lines) if line.startswith("### ")]
    # First ### should be Error for:, then Instruction
    if len(section_starts) >= 2:
        err_idx = next(i for i in section_starts if "### Error for:" in lines[i])
        instr_idx = next(i for i in section_starts if "### Instruction" in lines[i])
        assert err_idx < instr_idx, (
            f"### Error for: (line {err_idx}) must come before "
            f"### Instruction (line {instr_idx})"
        )


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


def test_retry_message_lists_invalid_artifact():
    """When the model writes to an artifact not in exposes, the retry message
    must list it under 'Failed output artifacts:' with an error."""
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
    first_resp = (
        "I will write to a bad artifact!"
        "\n\n>bad.sxpb\n(bad)"
        "\n\nAnd a good one"
        "\n\n>allowed.sxpb\n(good)\n"
    )
    retry_msg = _capture_retry_message(pipeline, session, first_resp)

    assert retry_msg is not None, "No retry message was generated"
    _assert_retry_format(retry_msg)

    # Must list bad.sxpb as failed
    assert "- bad.sxpb" in retry_msg, (
        f"'bad.sxpb' should be listed under Failed output artifacts:\n{retry_msg}"
    )
    # Must have an error for bad.sxpb
    assert "### Error for: bad.sxpb" in retry_msg, (
        f"Expected '### Error for: bad.sxpb' in:\n{retry_msg}"
    )
    # No accepted in failed section
    assert (
        "Accepted:" not in retry_msg.split("Failed output artifacts:")[1].split("\n")[0]
    ), "'Accepted:' should not appear before '### Instruction'"


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
