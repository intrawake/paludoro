from unittest.mock import AsyncMock, patch

import pytest

from paludoro.agent_pipeline import AgentPipeline
from paludoro.state import PaludoroSession


def test_rollback_turn_restores_previous_values_and_removes_new_artifacts():
    session = PaludoroSession(default_artifacts={"mood.sxpb": "(mood calm)"})
    session.reset()

    first = session.begin_chat_turn("Cheer up")
    session.save_artifact("mood.sxpb", "(mood happy)", turn=first.id)
    session.save_artifact("new.txt", "made by turn one", turn=first.id)

    second = session.begin_chat_turn("Be sleepy")
    session.save_artifact("mood.sxpb", "(mood sleepy)", turn=second.id)
    session.save_artifact("new.txt", "changed by turn two", turn=second.id)

    affected, _ = session.rollback_turn(second.id)

    assert set(affected) == {"mood.sxpb", "new.txt"}
    assert session.artifacts["mood.sxpb"] == "(mood happy)"
    assert session.artifacts["new.txt"] == "made by turn one"
    assert all(
        revision.turn_id != second.id
        for versions in session.artifact_versions.values()
        for revision in versions
    )

    session.rollback_turn(first.id)
    assert session.artifacts["mood.sxpb"] == "(mood calm)"
    assert "new.txt" not in session.artifacts


def test_rollback_preserves_later_manual_edit():
    session = PaludoroSession(default_artifacts={"mood.sxpb": "(mood calm)"})
    session.reset()
    turn = session.begin_chat_turn("Change the mood")
    session.save_artifact("mood.sxpb", "(mood happy)", turn=turn.id)
    session.save_artifact("mood.sxpb", "(mood custom)", source="manual")

    session.rollback_turn(turn.id)

    assert session.artifacts["mood.sxpb"] == "(mood custom)"
    assert [revision.source for revision in session.revisions_for("mood.sxpb")] == [
        "default",
        "manual",
    ]


def test_hidden_mutations_are_rolled_back():
    session = PaludoroSession()
    session.save_artifact("chat_history.sxpb.old", "old history", source="legacy")
    turn = session.begin_chat_turn("Next")
    session.save_artifact(
        "chat_history.sxpb.old",
        "",
        turn=turn.id,
        create_version=False,
        source="internal",
    )

    assert session.artifacts["chat_history.sxpb.old"] == ""
    assert session.visible_versions("chat_history.sxpb.old") == [["old history", None]]

    session.rollback_turn(turn.id)
    assert session.artifacts["chat_history.sxpb.old"] == "old history"


def test_legacy_version_numbers_are_not_migrated_as_turn_ids():
    session = PaludoroSession()
    session.from_dict(
        {
            "artifacts": {"mood.sxpb": "new"},
            "artifact_versions": {"mood.sxpb": [["old", 0], ["new", 1]]},
        }
    )

    assert [revision.turn_id for revision in session.revisions_for("mood.sxpb")] == [
        None,
        None,
    ]
    session.rollback_turn(1)
    assert session.artifacts["mood.sxpb"] == "new"


@pytest.mark.asyncio
async def test_pipeline_tags_entire_generated_cascade_with_one_turn():
    config = {
        "agent_dict": {
            "transcript": {
                "generate_as": {
                    "transcript": {
                        "expose": "chat_history.sxpb",
                        "remote_by_artipath": {
                            "/dev/stdin": {"name": "User"},
                            "dialogue_line.txt": {"name": "Assistant"},
                        },
                    }
                }
            },
            "main": {
                "generate_as": {
                    "text": {"model": {"name": "mock", "api_url": "http://mock"}}
                },
                "prompt_as": {"remote": "chat_history.sxpb"},
                "exposes": ["dialogue_line.txt", "look.txt"],
            },
            "avatar": {
                "generate_as": {
                    "text": {"model": {"name": "mock", "api_url": "http://mock"}}
                },
                "prompt_as": {"remote": "look.txt"},
                "exposes": ["image.txt"],
            },
            "draw": {
                "generate_as": {
                    "image": {
                        "model": {"name": "mock", "api_url": "http://mock"},
                        "expose": "image.png",
                    }
                },
                "prompt_as": {"remote": "image.txt"},
            },
        }
    }
    session = PaludoroSession()
    turn = session.begin_chat_turn("Hello")
    session.save_artifact(
        "/dev/stdin",
        turn.user_message,
        turn=turn.id,
        create_version=False,
        source="chat-input",
    )

    async def fake_text(*args, **kwargs):
        if kwargs["agent_name"] == "main":
            return ">dialogue_line.txt\nHi!\n\n>look.txt\nA sleepy red panda.\n"
        return ">image.txt\nA painted sleepy red panda.\n"

    with (
        patch(
            "paludoro.agent_pipeline.call_api",
            new=AsyncMock(side_effect=fake_text),
        ),
        patch(
            "paludoro.agent_pipeline.call_image_api",
            new=AsyncMock(return_value="data:image/png;base64,AAAA"),
        ),
    ):
        pipeline = AgentPipeline(config, session, turn_id=turn.id)
        await pipeline.on_artifacts_changed(["/dev/stdin"])

    expected = {
        "/dev/stdin",
        "chat_history.sxpb",
        "dialogue_line.txt",
        "look.txt",
        "image.txt",
        "image.png",
    }
    assert expected <= set(session.artifact_versions)
    for artipath in expected:
        assert {revision.turn_id for revision in session.revisions_for(artipath)} == {
            turn.id
        }

    session.rollback_turn(turn.id)
    assert not expected.intersection(session.artifacts)
