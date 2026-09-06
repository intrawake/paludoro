from fastapi.testclient import TestClient
import sxpb
from sxpb.types import SxpbMany

import paludoro.server as server
from paludoro.state import PaludoroSession


client = TestClient(server.app)


def history(*entries):
    return sxpb.dumps({"history": SxpbMany([{role: text} for role, text in entries])})


def test_delete_last_turn_rolls_back_all_generated_outputs(monkeypatch, tmp_path):
    session = PaludoroSession(default_artifacts={"mood.sxpb": "(mood calm)"})
    session.reset()
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    monkeypatch.setattr(server, "IMAGES_DIR", images_dir)

    first = session.begin_chat_turn("Hello")
    session.save_artifact(
        "chat_history.sxpb",
        history(("User", "Hello"), ("Ritika", "Hi")),
        turn=first.id,
        source="transcript",
    )
    session.save_artifact("mood.sxpb", "(mood happy)", turn=first.id)
    session.save_artifact("memory.txt", "first memory", turn=first.id)

    second = session.begin_chat_turn("Sleep")
    session.save_artifact(
        "chat_history.sxpb",
        history(
            ("User", "Hello"),
            ("Ritika", "Hi"),
            ("User", "Sleep"),
            ("Ritika", "Good night"),
        ),
        turn=second.id,
        source="transcript",
    )
    session.save_artifact("mood.sxpb", "(mood sleepy)", turn=second.id)
    session.save_artifact("memory.txt", "second memory", turn=second.id)
    image_uri = "/images/turn-two.png"
    (images_dir / "turn-two.png").write_bytes(b"png")
    session.save_artifact("image.png", image_uri, turn=second.id)
    session.save_artifact("notes.txt", "manual note", source="manual")

    response = client.delete("/api/history/last-turn")

    assert response.status_code == 200
    data = response.json()
    assert data["deleted_turn_id"] == second.id
    assert data["history"] == [
        {"role": "User", "content": "Hello", "raw_content": "Hello"},
        {"role": "Ritika", "content": "Hi", "raw_content": "Hi"},
    ]
    assert session.artifacts["mood.sxpb"] == "(mood happy)"
    assert session.artifacts["memory.txt"] == "first memory"
    assert session.artifacts["notes.txt"] == "manual note"
    assert "image.png" not in session.artifacts
    assert not (images_dir / "turn-two.png").exists()
    assert [turn.id for turn in session.chat_turns] == [first.id]

    stored = server.json.loads((tmp_path / "artifacts.json").read_text())
    assert stored["schema_version"] == 2
    assert stored["chat_turns"] == [{"id": first.id, "user_message": "Hello"}]


def test_reroll_removes_old_products_before_regenerating(monkeypatch, tmp_path):
    session = PaludoroSession(default_artifacts={"mood.sxpb": "(mood calm)"})
    session.reset()
    turn = session.begin_chat_turn("Try again")
    session.save_artifact(
        "chat_history.sxpb",
        history(("User", "Try again"), ("Ritika", "Old answer")),
        turn=turn.id,
        source="transcript",
    )
    session.save_artifact("mood.sxpb", "(mood old)", turn=turn.id)
    session.save_artifact("optional.txt", "stale output", turn=turn.id)
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")

    observed = {}

    class ReplacementPipeline:
        def __init__(self, config, pipeline_session, turn_id=None):
            self.session = pipeline_session
            self.turn_id = turn_id

        async def on_artifacts_changed(self, changed):
            observed["changed"] = changed
            observed["mood_before"] = self.session.artifacts["mood.sxpb"]
            observed["optional_before"] = "optional.txt" in self.session.artifacts
            observed["input"] = self.session.artifacts["/dev/stdin"]
            self.session.save_artifact(
                "dialogue_line.txt", "Replacement", turn=self.turn_id
            )

    monkeypatch.setattr(server, "AgentPipeline", ReplacementPipeline)

    response = client.post("/api/chat", json={"reroll": True})

    assert response.status_code == 200
    assert response.json()["turn_id"] == turn.id
    assert observed == {
        "changed": ["/dev/stdin"],
        "mood_before": "(mood calm)",
        "optional_before": False,
        "input": "Try again",
    }
    assert session.artifacts["dialogue_line.txt"] == "Replacement"
    assert session.revisions_for("dialogue_line.txt")[-1].turn_id == turn.id
    assert session.last_chat_turn() == turn


def test_delete_last_turn_refuses_active_pipeline(monkeypatch, tmp_path):
    session = PaludoroSession()
    turn = session.begin_chat_turn("Still running")
    session.active_turn_ids.add(turn.id)
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")

    response = client.delete("/api/history/last-turn")

    assert response.status_code == 409
    assert session.last_chat_turn() == turn


def test_delete_legacy_turn_refuses_unsafe_artifact_guess(monkeypatch, tmp_path):
    session = PaludoroSession()
    session.save_artifact(
        "chat_history.sxpb",
        history(("User", "Legacy"), ("Ritika", "Old response")),
        source="legacy",
    )
    session.save_artifact("mood.sxpb", "unknown owner", source="legacy")
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")

    response = client.delete("/api/history/last-turn")

    assert response.status_code == 409
    assert session.artifacts["mood.sxpb"] == "unknown owner"
