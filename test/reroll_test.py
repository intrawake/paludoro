from collections.abc import MutableMapping, MutableSequence
from typing import cast
from fastapi.testclient import TestClient
from paludoro.server import app, global_session
import sxpb
from sxpb.types import SxpbMany
from unittest.mock import patch, AsyncMock

client = TestClient(app)


def setup_function():
    global_session.reset()
    global_session.artifacts.clear()
    global_session.artifact_versions.clear()


def test_reroll_triggers_on_user_last_message():
    """If the last message is from the user (e.g. pipeline failed, nothing
    to pop), reroll must still trigger the pipeline to generate a response."""
    history = SxpbMany([{"Alice": "Hello, are you there?"}])

    with patch(
        "paludoro.server.config",
        {
            "agent_dict": {
                "transcript": {
                    "generate_as": {
                        "transcript": {
                            "remote_by_artipath": {"/dev/stdin": {"name": "Alice"}}
                        }
                    }
                }
            }
        },
    ):
        history_str = sxpb.dumps({"history": history})
        global_session.save_artifact("chat_history.sxpb", history_str)

        with patch("paludoro.server.AgentPipeline") as MockPipeline:
            mock_instance = MockPipeline.return_value
            mock_instance.on_artifacts_changed = AsyncMock()

            response = client.post("/api/chat", json={"reroll": True})
            assert response.status_code == 200

            # Pipeline MUST run — user wants a (re)generation
            MockPipeline.assert_called()
            mock_instance.on_artifacts_changed.assert_called_with(["chat_history.sxpb"])

            # User message should be untouched (nothing to pop)
            new_history_str = global_session.artifacts.get("chat_history.sxpb")
            assert isinstance(new_history_str, str)
            parsed = sxpb.loads(new_history_str, precise=True)
            assert isinstance(parsed, MutableMapping)
            new_history = cast(dict, parsed).get("history", [])
            assert len(new_history) == 1
            assert "Alice" in new_history[0]


def test_reroll_pops_assistant_message():
    # 1. Setup session with history ending in Assistant message
    history = SxpbMany([{"Alice": "Hello"}, {"Bob": "Hi there!"}])

    # Mock config to define Alice as user, Bob as assistant
    with patch(
        "paludoro.server.config",
        {
            "agent_dict": {
                "transcript": {
                    "generate_as": {
                        "transcript": {
                            "remote_by_artipath": {
                                "/dev/stdin": {"name": "Alice"},
                                "dialogue_line.txt": {"name": "Bob"},
                            }
                        }
                    }
                }
            }
        },
    ):
        history_str = sxpb.dumps({"history": history})
        global_session.save_artifact("chat_history.sxpb", history_str)

        with patch("paludoro.server.AgentPipeline") as MockPipeline:
            mock_instance = MockPipeline.return_value
            mock_instance.on_artifacts_changed = AsyncMock()

            response = client.post("/api/chat", json={"reroll": True})
            assert response.status_code == 200

            # Check that the history artifact was modified (Assistant message popped)
            new_history_str = global_session.artifacts.get("chat_history.sxpb")
            assert isinstance(new_history_str, str)
            parsed = sxpb.loads(new_history_str, precise=True)
            assert isinstance(parsed, MutableMapping)
            parsed_dict = cast(dict, parsed)
            new_history = parsed_dict.get("history", [])
            assert isinstance(new_history, MutableSequence)
            assert len(new_history) == 1
            assert "Alice" in new_history[0]

            # Verify pipeline triggered
            mock_instance.on_artifacts_changed.assert_called_with(["chat_history.sxpb"])
