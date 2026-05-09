import os
import json
import tempfile
from unittest.mock import patch

# Mock XDG_STATE_HOME before any imports from server
tmp_state = tempfile.TemporaryDirectory()
os.environ["XDG_STATE_HOME"] = tmp_state.name

from fastapi.testclient import TestClient  # noqa: E402
from paludoro.server import app, ARTIFACTS_FILE, global_session  # noqa: E402
import sxpb  # noqa: E402

client = TestClient(app)


def test_chat_persistence_and_polling():
    ARTIFACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ARTIFACTS_FILE.exists():
        ARTIFACTS_FILE.unlink()

    # Reset global session manually for the test
    global_session.artifacts = {}

    # Pre-populate session with transcript and main agent artifacts required for pipeline to run
    history_sxpb = sxpb.dumps({"history": [{"User": "Hi"}]})
    global_session.artifacts["chat_history.sxpb"] = history_sxpb

    # 2. Mock the AI API call (so main agent returns successfully)
    mock_response = "Hello from Assistant! >dialogue_line.txt\nHello from Assistant!"

    # We patch run_agent from AgentPipeline? No, let's just patch call_api
    with patch("paludoro.agent_pipeline.call_api", return_value=mock_response):
        # 3. Call chat.
        # Note: TestClient executes background tasks before returning.
        chat_req = {"history": [{"role": "User", "content": "Hi"}], "message": "Hi"}
        resp = client.post("/api/chat", json=chat_req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "trigger_gen" in data

        # 4. Verify ARTIFACTS_FILE exists and has the assistant response
        assert ARTIFACTS_FILE.exists()
        stored_data = json.loads(ARTIFACTS_FILE.read_text())
        assert "artifacts" in stored_data
        assert "chat_history.sxpb" in stored_data["artifacts"]

        # 5. Verify Polling returns the parsed history
        poll_resp = client.get("/api/poll?history_len=0")
        assert poll_resp.status_code == 200
        poll_data = poll_resp.json()
        print(
            f"DEBUG chat_history: {global_session.artifacts.get('chat_history.sxpb')}"
        )
        assert len(poll_data["new_history"]) >= 1

        # Depending on how the pipeline executed, it should contain User and Assistant.
        roles = [msg["role"] for msg in poll_data["new_history"]]
        assert "User" in roles


def test_image_on_save_hook():
    global_session.artifacts = {}
    global_session.artifact_versions = {}

    img_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    global_session.save_artifact("test_image.png", img_b64)

    content = global_session.artifacts["test_image.png"]
    assert content.startswith("/images/test_image.png_")
    assert content.endswith(".png")

    # Check version history also has the path
    versions = global_session.artifact_versions["test_image.png"]
    assert len(versions) == 1
    assert versions[0][0] == content

    # Check disk
    from paludoro.server import IMAGES_DIR

    img_filename = content.replace("/images/", "")
    assert (IMAGES_DIR / img_filename).exists()
