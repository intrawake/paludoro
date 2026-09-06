import asyncio
from unittest.mock import Mock

from fastapi import BackgroundTasks
import pytest

import paludoro.server as server
from paludoro.state import PaludoroSession


@pytest.fixture
def session(monkeypatch, tmp_path):
    session = PaludoroSession()
    monkeypatch.setattr(server, "global_session", session)
    monkeypatch.setattr(server, "ARTIFACTS_FILE", tmp_path / "artifacts.json")
    return session


@pytest.mark.asyncio
async def test_chat_accepts_fifo_while_pipeline_runs_without_overwriting_input(
    session, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()
    observed = []

    class Pipeline:
        def __init__(self, config, pipeline_session, turn_id=None):
            self.session = pipeline_session
            self.turn_id = turn_id

        async def on_artifacts_changed(self, changed):
            assert changed == ["/dev/stdin"]
            text = self.session.artifacts["/dev/stdin"]
            if text == "first":
                started.set()
                await release.wait()
                assert self.session.artifacts["/dev/stdin"] == "first"
            observed.append((self.turn_id, text))
            self.session.save_artifact("result.txt", text, turn=self.turn_id)

    monkeypatch.setattr(server, "AgentPipeline", Pipeline)
    first_tasks = BackgroundTasks()
    first = await server.chat(server.ChatRequest(message="first"), first_tasks)
    assert "/dev/stdin" not in session.artifacts
    worker = asyncio.create_task(first_tasks())
    try:
        await asyncio.wait_for(started.wait(), 2)
        second_tasks = BackgroundTasks()
        second = await server.chat(server.ChatRequest(message="second"), second_tasks)
        assert isinstance(second, dict)
        assert not second_tasks.tasks  # Only the first request starts the worker.
        assert session.artifacts["/dev/stdin"] == "first"
        assert session.active_turn_ids == {first["turn_id"], second["turn_id"]}
        poll = await server.poll_updates()
        assert poll["chat_busy"] is True
        assert poll["queued_messages"] == [
            {"turn_id": second["turn_id"], "message": "second"}
        ]
        assert (await server.delete_last_turn()).status_code == 409
        assert (await server.post_history(server.ChatRequest())).status_code == 409
        reroll = await server.chat(server.ChatRequest(reroll=True), BackgroundTasks())
        assert reroll.status_code == 409
        release.set()
        await worker
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    assert observed == [(first["turn_id"], "first"), (second["turn_id"], "second")]
    revisions = session.revisions_for("result.txt")
    assert [(r.turn_id, r.content) for r in revisions] == observed
    assert not session.active_turn_ids
    assert not session.chat_busy
    assert not session.pending_chat_work
    assert session.pipeline_triggers == session.pipeline_finishes == 2
    deleted = await server.delete_last_turn()
    assert deleted["deleted_turn_id"] == second["turn_id"]
    assert session.artifacts["result.txt"] == "first"
    assert session.artifacts["/dev/stdin"] == "first"


@pytest.mark.asyncio
async def test_failed_pipeline_does_not_stall_next_queued_message(session, monkeypatch):
    observed = []

    class Pipeline:
        def __init__(self, config, pipeline_session, turn_id=None):
            self.session = pipeline_session

        async def on_artifacts_changed(self, changed):
            text = self.session.artifacts["/dev/stdin"]
            observed.append(text)
            if text == "fail":
                raise RuntimeError("Model unavailable")

    monkeypatch.setattr(server, "AgentPipeline", Pipeline)
    tasks = BackgroundTasks()
    await server.chat(server.ChatRequest(message="fail"), tasks)
    await server.chat(server.ChatRequest(message="next"), BackgroundTasks())
    await tasks()
    assert observed == ["fail", "next"]
    assert not session.chat_busy
    assert not session.active_turn_ids
    assert session.pipeline_finishes == 2


@pytest.mark.asyncio
async def test_cancelled_worker_clears_runtime_busy_state(session, monkeypatch):
    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        async def on_artifacts_changed(self, changed):
            raise asyncio.CancelledError()

    monkeypatch.setattr(server, "AgentPipeline", Pipeline)
    tasks = BackgroundTasks()
    await server.chat(server.ChatRequest(message="first"), tasks)
    await server.chat(server.ChatRequest(message="pending"), BackgroundTasks())
    with pytest.raises(asyncio.CancelledError):
        await tasks()
    assert not session.chat_busy
    assert not session.active_turn_ids
    assert not session.pending_chat_work


@pytest.mark.asyncio
async def test_poll_idle_after_reload_despite_unbalanced_old_counters(session):
    session.from_dict({"pipeline_triggers": 10, "pipeline_finishes": 9})
    poll = await server.poll_updates()
    assert poll["pipeline_triggers"] > poll["pipeline_finishes"]
    assert poll["chat_busy"] is False
    assert poll["queued_messages"] == []


@pytest.mark.asyncio
async def test_failed_persistence_does_not_queue_or_poison_next_request(
    session, monkeypatch
):
    persist = Mock(side_effect=OSError("Disk unavailable"))
    monkeypatch.setattr(server, "persist_state", persist)
    tasks = BackgroundTasks()
    result = await server.chat(server.ChatRequest(message="not accepted"), tasks)
    assert result.status_code == 500
    assert not tasks.tasks
    assert not session.chat_turns
    assert not session.pending_chat_work
    assert not session.chat_busy
    assert session.pipeline_triggers == session.pipeline_finishes == 0
