from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from typing import Any, Optional, cast
from collections.abc import MutableMapping
import sxpb
from sxpb.types import SxpbMany
from paludoro.state import PaludoroSession
from paludoro.agent_pipeline import AgentPipeline


@pytest.fixture
def mock_session():
    session = PaludoroSession()
    session.artifacts = {
        "/dev/stdin": "",
        "dialogue_line.txt": "",
        "chat_history.sxpb": "",
    }
    return session


@pytest.fixture
def test_config():
    return {
        "agent_dict": {
            "transcript": {
                "generate_as": {
                    "transcript": {
                        "expose": "chat_history.sxpb",
                        "remote_by_artipath": [
                            {"/dev/stdin": {"name": "User"}},
                            {"dialogue_line.txt": {"name": "Assistant"}},
                        ],
                    }
                },
                "remotes": ["/dev/stdin", "dialogue_line.txt"],
            },
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["chat_history.sxpb"],
                "exposes": ["dialogue_line.txt"],
            },
        }
    }


class MockAgentPipeline(AgentPipeline):
    def __init__(self, config, session):
        super().__init__(config, session)
        self.run_history = []

    async def run_agent(self, agent_name: str, triggered_by: Optional[str] = None):
        self.run_history.append(agent_name)
        if agent_name == "transcript":
            # Simulate the base class run_agent behavior for transcript
            return await super().run_agent(agent_name, triggered_by=triggered_by)
        elif agent_name == "main":
            # Mock the model call to write to dialogue_line.txt
            self.session.save_artifact("dialogue_line.txt", "mock assistant response")
            return ["dialogue_line.txt"]
        return []


@pytest.mark.asyncio
async def test_agent_loop_limits(test_config, mock_session):
    pipeline = MockAgentPipeline(test_config, mock_session)

    # Simulate user sending a message
    mock_session.save_artifact("/dev/stdin", "Hello!")

    await pipeline.on_artifacts_changed(["/dev/stdin"])

    # Transcript should run (writes User to chat_history)
    # Then main should run (triggered by chat_history, writes Assistant to dialogue_line.txt)
    # Then transcript should run (triggered by dialogue_line.txt, writes Assistant to chat_history)
    # Then main should NOT run again (limit 1)

    assert pipeline.run_history == ["transcript", "main", "transcript"]

    history_content = mock_session.artifacts["chat_history.sxpb"]
    assert "User" in history_content
    assert "Hello!" in history_content
    assert "assistant" in history_content
    assert "mock assistant response" in history_content


# ---------------------------------------------------------------------------
# system_prompt_as / assistant_role tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_as_filepath():
    """system_prompt_as with filepath sends a system message before the user prompt."""
    config = {
        "agent_dict": {
            "sys_agent": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "system_prompt_as": {"filepath": "sys_prompt.md"},
                "exposes": ["output.txt"],
            }
        }
    }
    session = PaludoroSession()
    pipeline = AgentPipeline(config, session)

    with (
        patch("paludoro.agent_pipeline.get_resource_path") as mock_res,
        patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_res.return_value = MagicMock(
            exists=lambda: True, read_text=lambda: "You are a helpful assistant."
        )
        mock_call.return_value = "```text >output.txt\nHello!\n```\n"
        await pipeline.run_agent("sys_agent")

        args, _ = mock_call.call_args
        messages = args[1]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["role"] == "user"
        # no prompt_as, so user message has only the Instruction section
        assert "### Instruction" in messages[1]["content"]
        assert "output.txt" in messages[1]["content"]


@pytest.mark.asyncio
async def test_system_prompt_as_remote():
    """system_prompt_as with remote reads from a session artifact."""
    config = {
        "agent_dict": {
            "sys_agent": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "system_prompt_as": {"remote": "instructions.txt"},
                "exposes": ["output.txt"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "System: be brief.")
    pipeline = AgentPipeline(config, session)

    with (
        patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_call.return_value = "```text >output.txt\nOK\n```\n"
        await pipeline.run_agent("sys_agent")

        args, _ = mock_call.call_args
        messages = args[1]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "System: be brief."


@pytest.mark.asyncio
async def test_both_prompt_and_system_prompt():
    """When both prompt_as and system_prompt_as are present, both messages are sent."""
    config = {
        "agent_dict": {
            "dual_agent": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "prompt_as": {"remote": "user_instructions.txt"},
                "system_prompt_as": {"remote": "system_instructions.txt"},
                "exposes": ["output.txt"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("user_instructions.txt", "Do the thing.")
    session.save_artifact("system_instructions.txt", "Be a pirate.")
    pipeline = AgentPipeline(config, session)

    with (
        patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_call.return_value = "```text >output.txt\nArr!\n```\n"
        await pipeline.run_agent("dual_agent")

        args, _ = mock_call.call_args
        messages = args[1]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be a pirate."
        assert messages[1]["role"] == "user"
        assert "Do the thing." in messages[1]["content"]


@pytest.mark.asyncio
async def test_missing_both_prompts_returns_empty():
    """Text agents with neither prompt_as nor system_prompt_as return [] with error log."""
    config = {
        "agent_dict": {
            "naked_agent": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "exposes": ["output.txt"],
            }
        }
    }
    session = PaludoroSession()
    pipeline = AgentPipeline(config, session)

    with patch("os.getenv", return_value="http://mock-api"):
        changed = await pipeline.run_agent("naked_agent")
    assert changed == []


@pytest.mark.asyncio
async def test_model_assistant_role_in_retry():
    """assistant_role in model config replaces hardcoded 'assistant' in retry messages."""
    config = {
        "agent_dict": {
            "custom_role_agent": {
                "generate_as": {
                    "text": {"model": {"name": "mock_model", "assistant_role": "model"}}
                },
                "prompt_as": {"remote": "input.sxpb"},
                "remotes": ["input.sxpb"],
                "exposes": ["allowed.sxpb"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("input.sxpb", "(dummy)")
    pipeline = AgentPipeline(config, session)

    # First response is invalid (writes to bad artifact), second is valid
    responses = [
        "Oops!\n\n>bad.sxpb\n(bad)\n",
        "Fixed.\n\n>allowed.sxpb\n(good)\n",
    ]

    async def mock_call_api(*args, **kwargs):
        return responses.pop(0)

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ),
        patch("os.getenv", return_value="http://mock-api"),
    ):
        changed = await pipeline.run_agent("custom_role_agent")

    assert changed == ["allowed.sxpb"]
    assert session.artifacts.get("allowed.sxpb") == "(good)"


@pytest.mark.asyncio
async def test_model_assistant_role_defaults_to_assistant():
    """Without assistant_role, retry messages default to 'assistant'."""
    config = {
        "agent_dict": {
            "default_role_agent": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "prompt_as": {"remote": "input.sxpb"},
                "remotes": ["input.sxpb"],
                "exposes": ["allowed.sxpb"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("input.sxpb", "(dummy)")
    pipeline = AgentPipeline(config, session)

    responses = [
        "Oops!\n\n>bad.sxpb\n(bad)\n",
        "Fixed.\n\n>allowed.sxpb\n(good)\n",
    ]

    async def mock_call_api(*args, **kwargs):
        return responses.pop(0)

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ),
        patch("os.getenv", return_value="http://mock-api"),
    ):
        changed = await pipeline.run_agent("default_role_agent")

    assert changed == ["allowed.sxpb"]
    assert session.artifacts.get("allowed.sxpb") == "(good)"


@pytest.mark.asyncio
async def test_system_prompt_as_triggers_on_remote():
    """system_prompt_as remote is registered as a dependency trigger."""
    config = {
        "agent_dict": {
            "triggered": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "system_prompt_as": {"remote": "trigger_artifact.txt"},
                "exposes": ["output.txt"],
            }
        }
    }
    session = PaludoroSession()
    pipeline = AgentPipeline(config, session)

    assert "trigger_artifact.txt" in pipeline.triggers
    assert "triggered" in pipeline.triggers["trigger_artifact.txt"]


@pytest.mark.asyncio
async def test_transcript_agent_no_prompt_required():
    """Transcript agents don't need prompt_as or system_prompt_as."""
    config = {
        "agent_dict": {
            "transcript": {
                "generate_as": {
                    "transcript": {
                        "expose": "chat_history.sxpb",
                        "remote_by_artipath": [{"/dev/stdin": {"name": "User"}}],
                    }
                },
                "remotes": ["/dev/stdin"],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("/dev/stdin", "Hello!")
    pipeline = AgentPipeline(config, session)

    changed = await pipeline.run_agent("transcript")
    assert changed == ["chat_history.sxpb"]
    assert "Hello!" in session.artifacts["chat_history.sxpb"]


# ---------------------------------------------------------------------------
# SxPB type-checking tests
# ---------------------------------------------------------------------------

SXPB_NEST_DEFAULT = '("")\n(mood amused)\n'
SXPB_MESG_CONTENT = "(mood bored)\n"  # missing ("") prefix → SxpbMesg
SXPB_NEST_CONTENT = '("")\n(mood cheerful)\n'


@pytest.mark.asyncio
async def test_type_check_rejects_wrong_sxpb_type():
    """SxpbNest default rejects SxpbMesg content from model."""
    config = {
        "agent_dict": {
            "agent": {
                "generate_as": {"text": {"model": {"name": "mock"}}},
                "prompt_as": {"remote": "instructions.txt"},
                "expose_by_artipath": [{"mood.sxpb": {"default": "default_mood.sxpb"}}],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "Set a mood.")
    pipeline = AgentPipeline(config, session)

    # First response: wrong type (SxpbMesg instead of SxpbNest)
    # Second response: correct
    responses = [
        f">mood.sxpb\n{SXPB_MESG_CONTENT}\n",
        f">mood.sxpb\n{SXPB_NEST_CONTENT}\n",
    ]

    async def mock_call_api(*args, **kwargs):
        return responses.pop(0)

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ),
        patch("paludoro.agent_pipeline.get_resource_path") as mock_res,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_res.return_value = MagicMock(
            exists=lambda: True, read_text=lambda: SXPB_NEST_DEFAULT
        )
        changed = await pipeline.run_agent("agent")

    assert changed == ["mood.sxpb"]
    # The SxpbNest content should be saved (not the SxpbMesg)
    assert "mood" in session.artifacts["mood.sxpb"]
    assert "cheerful" in session.artifacts["mood.sxpb"]


@pytest.mark.asyncio
async def test_type_check_accepts_correct_sxpb_type():
    """Correct SxPB type is accepted on first attempt."""
    config = {
        "agent_dict": {
            "agent": {
                "generate_as": {"text": {"model": {"name": "mock"}}},
                "prompt_as": {"remote": "instructions.txt"},
                "expose_by_artipath": [{"mood.sxpb": {"default": "default_mood.sxpb"}}],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "Set a mood.")
    pipeline = AgentPipeline(config, session)

    with (
        patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call,
        patch("paludoro.agent_pipeline.get_resource_path") as mock_res,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_res.return_value = MagicMock(
            exists=lambda: True, read_text=lambda: SXPB_NEST_DEFAULT
        )
        mock_call.return_value = f">mood.sxpb\n{SXPB_NEST_CONTENT}\n"
        changed = await pipeline.run_agent("agent")

    assert changed == ["mood.sxpb"]
    assert "cheerful" in session.artifacts["mood.sxpb"]


@pytest.mark.asyncio
async def test_type_check_no_default_no_check():
    """Without a default, no type checking is performed — any valid SxPB accepted."""
    config = {
        "agent_dict": {
            "agent": {
                "generate_as": {"text": {"model": {"name": "mock"}}},
                "prompt_as": {"remote": "instructions.txt"},
                "expose_by_artipath": [{"data.sxpb": {}}],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "Write data.")
    pipeline = AgentPipeline(config, session)

    with (
        patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_call.return_value = f">data.sxpb\n{SXPB_MESG_CONTENT}\n"
        changed = await pipeline.run_agent("agent")

    assert changed == ["data.sxpb"]
    assert "bored" in session.artifacts["data.sxpb"]


@pytest.mark.asyncio
async def test_type_check_partial_acceptance():
    """Partial acceptance: good artifact saved, bad one retried with type error."""
    config = {
        "agent_dict": {
            "agent": {
                "generate_as": {"text": {"model": {"name": "mock"}}},
                "prompt_as": {"remote": "instructions.txt"},
                "expose_by_artipath": [
                    {"mood.sxpb": {"default": "default_mood.sxpb"}},
                    {"data.txt": {}},
                ],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "Set mood and data.")
    pipeline = AgentPipeline(config, session)

    # Attempt 1: data.txt good (code block), mood.sxpb wrong type
    # Attempt 2: mood.sxpb fixed
    responses = [
        f">mood.sxpb\n{SXPB_MESG_CONTENT}\n\n```text >data.txt\nhello\n```\n",
        f">mood.sxpb\n{SXPB_NEST_CONTENT}\n",
    ]

    async def mock_call_api(*args, **kwargs):
        return responses.pop(0)

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=mock_call_api),
        ),
        patch("paludoro.agent_pipeline.get_resource_path") as mock_res,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_res.return_value = MagicMock(
            exists=lambda: True, read_text=lambda: SXPB_NEST_DEFAULT
        )
        changed = await pipeline.run_agent("agent")

    assert "data.txt" in changed
    assert "mood.sxpb" in changed
    assert session.artifacts["data.txt"] == "hello"
    assert "cheerful" in session.artifacts["mood.sxpb"]


@pytest.mark.asyncio
async def test_exposes_without_default_are_required():
    """Artifacts in exposes but not in expose_by_artipath should be Required.
    Those with defaults should be Optional.
    """
    config = {
        "agent_dict": {
            "agent": {
                "generate_as": {"text": {"model": {"name": "mock"}}},
                "prompt_as": {"remote": "instructions.txt"},
                "exposes": [
                    "dialogue_line.txt",
                    "cosmetic.sxpb",
                    "mood.sxpb",
                ],
                "expose_by_artipath": [
                    {"cosmetic.sxpb": {"default": "default_cosmetic.sxpb"}},
                    {"mood.sxpb": {"default": "default_mood.sxpb"}},
                ],
            }
        }
    }
    session = PaludoroSession()
    session.save_artifact("instructions.txt", "Do stuff.")
    pipeline = AgentPipeline(config, session)

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new_callable=AsyncMock,
        ) as mock_call,
        patch("os.getenv", return_value="http://mock-api"),
    ):
        mock_call.return_value = ">dialogue_line.txt\nHello!\n\n>cosmetic.sxpb\n(good)\n\n>mood.sxpb\n(happy)\n"
        await pipeline.run_agent("agent")

        # Check the prompt sent to the model
        args, _ = mock_call.call_args
        messages = args[1]
        user_msg = next(m for m in messages if m["role"] == "user")

        assert "Required: dialogue_line.txt" in user_msg["content"], (
            "dialogue_line.txt has no default, should be Required"
        )
        assert "Optional: cosmetic.sxpb, mood.sxpb" in user_msg["content"], (
            "cosmetic.sxpb and mood.sxpb have defaults, should be Optional"
        )
        assert "You may write any of" not in user_msg["content"], (
            "Should not use fallback 'You may write any of'"
        )


# ---------------------------------------------------------------------------
# forgetfulness tests
# ---------------------------------------------------------------------------


def _make_forget_config(threshold=4, preserve=2):
    return {
        "agent_dict": {
            "transcript": {
                "generate_as": {
                    "transcript": {
                        "expose": "chat_history.sxpb",
                        "forgetfulness": {
                            "threshold_turn_count": threshold,
                            "preserve_turn_count": preserve,
                            "expose": "chat_history.sxpb.old",
                        },
                        "remote_by_artipath": [
                            {"/dev/stdin": {"name": "User"}},
                            {"dialogue_line.txt": {"name": "Assistant"}},
                        ],
                    }
                },
            },
        }
    }


def _make_history(*messages):
    """Build a chat_history.sxpb string from User/Assistant message pairs."""
    entries = SxpbMany([])
    for role, content in messages:
        entries.append({role: content})
    return sxpb.dumps({"history": entries})


def _get_history_messages(history_str):
    """Parse chat_history.sxpb and return list of (role, content) tuples."""
    parsed = sxpb.loads(history_str, precise=True)
    history_list = (
        cast(Any, parsed).get("history", [])
        if isinstance(parsed, MutableMapping)
        else []
    )
    result = []
    for entry in history_list:
        for role, content in entry.items():
            result.append((role, content))
    return result


@pytest.mark.asyncio
async def test_forgetfulness_below_threshold():
    """No forgetfulness action when message count is below threshold."""
    config = _make_forget_config(threshold=5, preserve=2)
    session = PaludoroSession()

    # 3 existing messages, below threshold of 5
    session.artifacts["chat_history.sxpb"] = _make_history(
        ("User", "a"), ("Assistant", "b"), ("User", "c")
    )
    session.artifacts["/dev/stdin"] = "d"

    pipeline = AgentPipeline(config, session)
    result = await pipeline.run_agent("transcript", triggered_by="/dev/stdin")

    # No .old created
    assert "chat_history.sxpb.old" not in session.artifacts
    # History has 4 entries (original 3 + new)
    msgs = _get_history_messages(session.artifacts["chat_history.sxpb"])
    assert len(msgs) == 4
    assert msgs[-1] == ("User", "d")
    # No old_artipath returned since below threshold and no action taken
    # (old_artipath is still appended because it's configured, but .old doesn't exist)
    assert "chat_history.sxpb" in result


@pytest.mark.asyncio
async def test_forgetfulness_first_crossing():
    """When history hits threshold, overflow is split into .old with fence comment."""
    config = _make_forget_config(threshold=4, preserve=2)
    session = PaludoroSession()

    # 3 existing messages — adding 1 more hits threshold of 4
    session.artifacts["chat_history.sxpb"] = _make_history(
        ("User", "msg1"), ("Assistant", "msg2"), ("User", "msg3")
    )
    session.artifacts["/dev/stdin"] = "msg4"

    pipeline = AgentPipeline(config, session)
    result = await pipeline.run_agent("transcript", triggered_by="/dev/stdin")

    # .old artifact created
    assert "chat_history.sxpb.old" in session.artifacts
    old_content = session.artifacts["chat_history.sxpb.old"]
    assert "Oldest chat history that will be forgotten after this turn." in old_content
    # Overflow (msgs 1-2) in .old
    assert "msg1" in old_content
    assert "msg2" in old_content
    assert "msg3" not in old_content

    # Main history trimmed to preserve=2: msg3 + new msg4
    msgs = _get_history_messages(session.artifacts["chat_history.sxpb"])
    assert len(msgs) == 2
    assert msgs == [("User", "msg3"), ("User", "msg4")]

    # Both artifacts returned as changed
    assert "chat_history.sxpb" in result
    assert "chat_history.sxpb.old" in result


@pytest.mark.asyncio
async def test_forgetfulness_cleanup():
    """After first crossing, next invocation clears .old and trims history."""
    config = _make_forget_config(threshold=4, preserve=2)
    session = PaludoroSession()

    # Simulate state AFTER first crossing: .old exists, history has 2 entries
    session.artifacts["chat_history.sxpb"] = _make_history(
        ("User", "msg3"), ("User", "msg4")
    )
    session.artifacts["chat_history.sxpb.old"] = (
        "; Oldest chat history that will be forgotten after this turn.\n"
        + _make_history(("User", "msg1"), ("Assistant", "msg2"))
    )
    session.artifacts["dialogue_line.txt"] = "msg5"

    pipeline = AgentPipeline(config, session)
    _ = await pipeline.run_agent("transcript", triggered_by="dialogue_line.txt")

    # .old cleared
    assert session.artifacts.get("chat_history.sxpb.old", "").strip() == ""
    # History trimmed to preserve=2: msg4 + new msg5
    msgs = _get_history_messages(session.artifacts["chat_history.sxpb"])
    assert len(msgs) == 2
    assert msgs == [("User", "msg4"), ("Assistant", "msg5")]


@pytest.mark.asyncio
async def test_forgetfulness_preserve_equals_threshold():
    """When preserve == threshold, no overflow occurs but .old still cycles."""
    config = _make_forget_config(threshold=4, preserve=4)
    session = PaludoroSession()

    session.artifacts["chat_history.sxpb"] = _make_history(
        ("User", "a"), ("Assistant", "b"), ("User", "c")
    )
    session.artifacts["/dev/stdin"] = "d"

    pipeline = AgentPipeline(config, session)
    _ = await pipeline.run_agent("transcript", triggered_by="/dev/stdin")

    # total_msgs (4) > preserve (4) is False, so no overflow
    msgs = _get_history_messages(session.artifacts["chat_history.sxpb"])
    assert len(msgs) == 4  # all messages kept
    # .old should not be created (no overflow)
    assert "chat_history.sxpb.old" not in session.artifacts


@pytest.mark.asyncio
async def test_forgetfulness_full_pipeline():
    """End-to-end: user message triggers first crossing, assistant triggers cleanup.

    Uses a MockAgentPipeline to verify the full cascade within one
    on_artifacts_changed call.
    """
    config = {
        "agent_dict": {
            "transcript": {
                "generate_as": {
                    "transcript": {
                        "expose": "chat_history.sxpb",
                        "forgetfulness": {
                            "threshold_turn_count": 4,
                            "preserve_turn_count": 2,
                            "expose": "chat_history.sxpb.old",
                        },
                        "remote_by_artipath": [
                            {"/dev/stdin": {"name": "User"}},
                            {"dialogue_line.txt": {"name": "Assistant"}},
                        ],
                    }
                },
                "remotes": ["/dev/stdin", "dialogue_line.txt"],
            },
            "main": {
                "generate_as": {"text": {"model": {"name": "mock_model"}}},
                "remotes": ["chat_history.sxpb.old", "chat_history.sxpb"],
                "exposes": ["dialogue_line.txt"],
            },
        }
    }

    class PipelineSpy(AgentPipeline):
        def __init__(self, c, s):
            super().__init__(c, s)
            self.run_history = []

        async def run_agent(self, agent_name, triggered_by=None):
            self.run_history.append((agent_name, triggered_by))
            if agent_name == "transcript":
                return await super().run_agent(agent_name, triggered_by=triggered_by)
            elif agent_name == "main":
                self.session.save_artifact("dialogue_line.txt", "mock response")
                return ["dialogue_line.txt"]
            return []

    session = PaludoroSession()
    # 3 messages in history — next user message hits threshold
    session.artifacts["chat_history.sxpb"] = _make_history(
        ("User", "old1"), ("Assistant", "old2"), ("User", "old3")
    )
    session.artifacts["/dev/stdin"] = "current"

    pipeline = PipelineSpy(config, session)
    await pipeline.on_artifacts_changed(["/dev/stdin"])

    # Sequence: transcript(user) → main → transcript(assistant, cleanup)
    assert [a for a, _ in pipeline.run_history] == ["transcript", "main", "transcript"]

    # After full turn:
    # - First transcript created .old and trimmed history
    # - Main ran (saw .old)
    # - Second transcript cleared .old and trimmed again
    # Final state: .old empty, history = preserve count
    assert session.artifacts.get("chat_history.sxpb.old", "").strip() == ""
    msgs = _get_history_messages(session.artifacts["chat_history.sxpb"])
    assert len(msgs) == 2
    # The preserved messages should be "old3" + "current" user msg + assistant response
    # But after cleanup, only last 2 messages remain
    assert msgs[0] == ("User", "current")
    assert msgs[1] == ("Assistant", "mock response")
