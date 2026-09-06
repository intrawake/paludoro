from copy import deepcopy
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

import paludoro.server as server
from paludoro.state import PaludoroSession


@pytest.fixture
def artifact_api(monkeypatch, tmp_path):
    session = PaludoroSession(default_artifacts={"test.sxpb": "(value original)"})
    session.reset()
    session.dirty_artifacts.clear()
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")
    return TestClient(server.app), session


@pytest.mark.parametrize("content", ["(value", "(value bad))", "(value bad)\n)\n)"])
@pytest.mark.parametrize("artipath", ["test.sxpb", "new.sxpb"])
def test_invalid_manual_sxpb_has_no_side_effects(artifact_api, artipath, content):
    client, session = artifact_api
    before = deepcopy(session.to_dict())
    hook = Mock(side_effect=lambda path, text: text)
    session.on_save_hooks.append(hook)

    response = client.put(f"/api/artifacts/{artipath}", json={"content": content})

    assert response.status_code == 400
    assert response.json()["error"].startswith("Invalid SxPB:")
    assert session.to_dict() == before
    assert not session.dirty_artifacts
    assert not server.ARTIFACTS_FILE.exists()
    hook.assert_not_called()


def test_valid_manual_sxpb_is_saved_and_persisted(artifact_api):
    client, session = artifact_api
    response = client.put(
        "/api/artifacts/test.sxpb", json={"content": "(value edited)"}
    )

    assert response.status_code == 200
    assert session.artifacts["test.sxpb"] == "(value edited)"
    revisions = session.revisions_for("test.sxpb")
    assert len(revisions) == 2
    assert revisions[-1].source == "manual"
    assert server.ARTIFACTS_FILE.exists()


def test_non_sxpb_manual_artifact_remains_free_form(artifact_api):
    client, session = artifact_api
    response = client.put("/api/artifacts/notes.txt", json={"content": "Not SxPB ("})

    assert response.status_code == 200
    assert session.artifacts["notes.txt"] == "Not SxPB ("
