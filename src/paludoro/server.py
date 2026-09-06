from collections.abc import MutableMapping, MutableSequence
import asyncio
import base64
import json
import logging
import os
import signal
import sys
import time
from typing import Any, cast
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import sxpb

from paludoro.agent_pipeline import AgentPipeline
from paludoro.api import get_llm_request_log, set_llm_log_maxlen
from paludoro.state import ChatWork, PaludoroSession
from paludoro.config import load_config, get_resource_path
from paludoro import otel_setup


# Filter out /api/poll from uvicorn.access logs to reduce clutter
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/poll" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DIST_DIR = BASE_DIR / "dist"
STATE_DIR = (
    Path(os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))) / "paludoro"
)
STATE_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_FILE = STATE_DIR / "artifacts.json"
IMAGES_DIR = STATE_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Config
config = load_config()
CHAT_HISTORY_ARTIFACT = config.get("chat_history", "chat_history.sxpb")

default_artifacts = {}
expose_filepaths = config.get("expose_filepaths", [])
if isinstance(expose_filepaths, MutableSequence):
    for f in expose_filepaths:
        p = get_resource_path(f)
        if p.exists():
            content = p.read_text()
            default_artifacts[f] = content

agent_dict = config.get("agent_dict", {})
for agent_name, agent_config in agent_dict.items():
    eba = agent_config.get("expose_by_artipath", [])
    if isinstance(eba, MutableSequence):
        for item in eba:
            if isinstance(item, MutableMapping):
                for artipath, details in item.items():
                    if isinstance(details, MutableMapping) and "default" in details:
                        default_f = details["default"]
                        p = get_resource_path(default_f)
                        if p.exists():
                            content = p.read_text()
                            if artipath not in default_artifacts:
                                default_artifacts[artipath] = content

global_session = PaludoroSession(default_artifacts=default_artifacts)


def image_save_hook(artipath: str, content: str) -> str:
    if isinstance(content, str) and content.startswith("data:image/png;base64,"):
        try:
            b64_str = content.split(",", 1)[1]
            img_data = base64.b64decode(b64_str)
            # Create a deterministic name based on hash of content to avoid duplicates
            import hashlib

            h = hashlib.md5(img_data).hexdigest()[:8]
            img_name = f"{artipath.replace('/', '_')}_{int(time.time())}_{h}.png"
            img_path = IMAGES_DIR / img_name
            img_path.write_bytes(img_data)
            return f"/images/{img_name}"
        except Exception as e:
            logging.error(f"Failed to save image for {artipath}: {e}")
    return content


global_session.on_save_hooks.append(image_save_hook)


def history_version_hook(artipath: str, content: str) -> str:
    if artipath == CHAT_HISTORY_ARTIFACT:
        global_session.history_version += 1
    return content


global_session.on_save_hooks.append(history_version_hook)


def persist_state():
    """Atomically persist the complete session state."""
    tmp_path = ARTIFACTS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(global_session.to_dict()))
    os.replace(tmp_path, ARTIFACTS_FILE)


def cleanup_unreferenced_images(candidate_contents):
    referenced = set(global_session.artifacts.values())
    for artipath in list(global_session.artifact_versions):
        referenced.update(
            revision.content for revision in global_session.revisions_for(artipath)
        )

    for content in set(candidate_contents):
        if not isinstance(content, str) or not content.startswith("/images/"):
            continue
        image_name = content.removeprefix("/images/")
        if not image_name or Path(image_name).name != image_name:
            continue
        if content not in referenced:
            (IMAGES_DIR / image_name).unlink(missing_ok=True)


def rollback_turn(turn_id: int):
    affected, removed_contents = global_session.rollback_turn(turn_id)
    if CHAT_HISTORY_ARTIFACT in affected:
        global_session.history_version += 1
    cleanup_unreferenced_images(removed_contents)
    return affected


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if ARTIFACTS_FILE.exists():
        try:
            stored = json.loads(ARTIFACTS_FILE.read_text())
            if "artifacts" in stored and isinstance(stored["artifacts"], dict):
                # New format
                global_session.from_dict(stored)

                # Migrate existing base64 to files
                for artipath, content in list(global_session.artifacts.items()):
                    new_content = image_save_hook(artipath, content)
                    if new_content != content:
                        global_session.artifacts[artipath] = new_content

                for artipath in list(global_session.artifact_versions):
                    for revision in global_session.revisions_for(artipath):
                        revision.content = image_save_hook(artipath, revision.content)
                    revisions = global_session.revisions_for(artipath)
                    if revisions:
                        global_session.artifacts[artipath] = revisions[-1].content
            else:
                # Old format (flat dict of artifacts)
                for k, v in stored.items():
                    global_session.save_artifact(k, v)
        except Exception as e:
            print(f"Failed to load artifacts.json: {e}")
    else:
        global_session.reset()

    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        # Shutdown
        print("\nPaludoro (FastAPI) shutting down...")
        try:
            persist_state()
        except Exception:
            pass


app = FastAPI(title="Paludoro", lifespan=lifespan)

otel_setup.init_otel("paludoro-server", config)
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except ImportError:
    pass

# Initialize LLM request log size from config (default 10)
_llm_log_size = config.get("llm_request_log_size", 10)
if isinstance(_llm_log_size, int) and _llm_log_size > 0:
    set_llm_log_maxlen(_llm_log_size)

_cached_history_str = ""
_cached_history = []


def parse_chat_history():
    global _cached_history_str, _cached_history
    history_str = global_session.artifacts.get(CHAT_HISTORY_ARTIFACT, "")
    if history_str == _cached_history_str:
        return _cached_history

    if not history_str:
        _cached_history_str = ""
        _cached_history = []
        return []
    try:
        parsed = sxpb.loads(history_str, precise=True)
        history_list = (
            cast(Any, parsed).get("history", [])
            if isinstance(parsed, MutableMapping)
            else []
        )
        out = []
        for item in history_list:
            for k, v in item.items():
                out.append({"role": k, "content": v, "raw_content": v})

        _cached_history_str = history_str
        _cached_history = out
        return out
    except Exception:
        return []


@app.get("/api/poll")
async def poll_updates():
    res_artifacts = {}
    for artipath in list(global_session.dirty_artifacts):
        res_artifacts[artipath] = global_session.artifacts.get(artipath, "")
    global_session.dirty_artifacts.clear()

    return {
        "artifacts": res_artifacts,
        "history_version": global_session.history_version,
        "running_agents": global_session.running_agents,
        "pipeline_triggers": global_session.pipeline_triggers,
        "pipeline_finishes": global_session.pipeline_finishes,
        "chat_busy": global_session.chat_busy,
        "queued_messages": [
            {"turn_id": work.turn_id, "message": work.message}
            for work in global_session.pending_chat_work
            if work.message is not None
        ],
    }


@app.get("/api/history")
async def get_history():
    return {"history": parse_chat_history()}


class Message(BaseModel):
    role: str
    content: str
    raw_content: str = ""


class ChatRequest(BaseModel):
    history: list[Message] = []
    message: Optional[str] = None
    reroll: bool = False


class ArtifactUpdateRequest(BaseModel):
    content: str


@app.post("/api/history")
async def post_history(req: ChatRequest):
    if global_session.chat_busy:
        return JSONResponse(
            status_code=409, content={"error": "A chat turn is still running"}
        )

    if not req.history:
        removed_contents = list(global_session.artifacts.values())
        global_session.reset()
        global_session.history_version += 1
        cleanup_unreferenced_images(removed_contents)
        try:
            persist_state()
        except Exception as e:
            print(f"Failed to save artifacts.json: {e}")
        return {"status": "ok"}

    # This endpoint remains for one-time localStorage migration. Once tracked
    # turns exist, history changes must use the causal chat APIs below.
    if global_session.chat_turns:
        return JSONResponse(
            status_code=409,
            content={"error": "Tracked chat history cannot be replaced directly"},
        )

    from sxpb.types import SxpbMany

    history_list = SxpbMany([])
    for msg in req.history:
        history_list.append({msg.role: msg.content})

    new_history_data = {"history": history_list}
    new_history_str = sxpb.dumps(new_history_data)
    global_session.save_artifact(
        CHAT_HISTORY_ARTIFACT, new_history_str, source="legacy"
    )

    try:
        persist_state()
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")

    return {"status": "ok"}


@app.delete("/api/history/last-turn")
async def delete_last_turn():
    turn = global_session.last_chat_turn()
    if turn is None:
        if parse_chat_history():
            return JSONResponse(
                status_code=409,
                content={
                    "error": "This turn predates causal tracking and cannot be safely rolled back"
                },
            )
        return JSONResponse(
            status_code=404, content={"error": "No chat turn to delete"}
        )
    if global_session.chat_busy:
        return JSONResponse(
            status_code=409, content={"error": "The last chat turn is still running"}
        )

    affected = rollback_turn(turn.id)
    global_session.chat_turns.pop()
    try:
        persist_state()
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to save state"})

    return {
        "status": "ok",
        "deleted_turn_id": turn.id,
        "history": parse_chat_history(),
        "affected_artifacts": affected,
        **artifact_response(),
    }


def get_user_role_name():
    agent_dict = config.get("agent_dict", {})
    transcript = agent_dict.get("transcript", {})
    if not transcript:
        return "User"
    generate_as = transcript.get("generate_as", {})
    # It might be an SxpbLone or a dict
    trans_config = generate_as.get("transcript", {})
    if not trans_config:
        return "User"
    rba = trans_config.get("remote_by_artipath", [])

    if isinstance(rba, MutableMapping):
        details = rba.get("/dev/stdin")
        if isinstance(details, MutableMapping):
            return details.get("name", "User")
    elif isinstance(rba, MutableSequence):
        for item in rba:
            if isinstance(item, MutableMapping) and "/dev/stdin" in item:
                details = item["/dev/stdin"]
                if isinstance(details, MutableMapping):
                    return details.get("name", "User")
    return "User"


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "project": "paludoro",
        "user_role": get_user_role_name(),
    }


def artifact_response():
    version_counts = {
        artipath: len(global_session.visible_versions(artipath))
        for artipath in global_session.artifact_versions
    }
    return {
        "artifacts": global_session.artifacts,
        "version_counts": version_counts,
    }


@app.get("/api/artifacts")
async def list_artifacts():
    """Return current artifact content and visible version counts."""
    return artifact_response()


@app.get("/api/artifacts/{artipath:path}/versions")
async def get_artifact_versions(artipath: str):
    """Return full version history for a single artifact (lazy load)."""
    versions = global_session.visible_versions(artipath)
    if not versions and artipath not in global_session.artifacts:
        return JSONResponse(status_code=404, content={"error": "Artifact not found"})
    return {"artipath": artipath, "versions": versions}


@app.put("/api/artifacts/{artipath:path}")
async def update_artifact(artipath: str, req: ArtifactUpdateRequest):
    """Validate edited content before saving a new artifact version."""
    if artipath.endswith(".sxpb"):
        try:
            sxpb.loads(req.content, precise=True)
        except Exception as e:
            return JSONResponse(
                status_code=400, content={"error": f"Invalid SxPB: {e}"}
            )
    global_session.save_artifact(artipath, req.content, source="manual")
    try:
        persist_state()
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")
    return {"status": "ok"}


@app.delete("/api/artifacts/{artipath:path}/versions/{version_index:int}")
async def delete_artifact_version(artipath: str, version_index: int):
    """Delete one visible version without cascading to its chat turn."""
    try:
        removed_contents = global_session.delete_visible_version(
            artipath, version_index
        )
    except IndexError:
        return JSONResponse(status_code=404, content={"error": "Version not found"})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    cleanup_unreferenced_images(removed_contents)
    try:
        persist_state()
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")

    return {
        "status": "ok",
        "artifact_versions": {
            name: global_session.visible_versions(name)
            for name in global_session.artifact_versions
        },
    }


@app.get("/api/agents")
async def get_agents():
    return {"agents": list(config.get("agent_dict", {}).keys())}


@app.get("/api/llm-requests")
async def get_llm_requests():
    return {"requests": get_llm_request_log()}


@app.post("/api/agents/{agent_name}/stop")
async def stop_agent(agent_name: str):
    if agent_name not in global_session.running_agents:
        return JSONResponse(
            status_code=404, content={"error": f"Agent {agent_name} is not running"}
        )
    # Kill in-flight HTTP requests by closing the agent's httpx client
    client = global_session.agent_http_clients.pop(agent_name, None)
    if client:
        await client.aclose()
    global_session.request_cancel(agent_name)
    return {"status": "ok"}


@app.post("/api/agents/{agent_name}/run")
async def run_specific_agent(agent_name: str, background_tasks: BackgroundTasks):
    agent_dict = config.get("agent_dict", {})
    if agent_name not in agent_dict:
        return JSONResponse(
            status_code=404, content={"error": f"Agent {agent_name} not found"}
        )

    global_session.pipeline_triggers += 1
    trigger_gen = global_session.pipeline_triggers

    async def run_agent_bg():
        try:
            pipeline = AgentPipeline(config, global_session)
            # We don't have a specific 'triggered_by' for on-demand runs
            await pipeline.run_agent(agent_name)
            try:
                persist_state()
            except Exception as e:
                print(f"Failed to save artifacts.json: {e}")
        except Exception as e:
            print(f"Manual agent run failed: {e}")
        finally:
            global_session.pipeline_finishes += 1

    background_tasks.add_task(run_agent_bg)
    return {"status": "ok", "trigger_gen": trigger_gen}


async def run_chat_queue():
    session = global_session
    try:
        while session.pending_chat_work:
            work = session.pending_chat_work.pop(0)
            try:
                if work.message is not None:
                    session.save_artifact(
                        "/dev/stdin",
                        work.message,
                        turn=work.turn_id,
                        create_version=False,
                        source="chat-input",
                    )
                pipeline = AgentPipeline(config, session, turn_id=work.turn_id)
                await pipeline.on_artifacts_changed(work.changed_artipaths)
            except Exception as e:
                print(f"Agent pipeline failed: {e}")
            finally:
                session.active_turn_ids.discard(work.turn_id)
                session.pipeline_finishes += 1
                try:
                    persist_state()
                except Exception as e:
                    print(f"Failed to save artifacts.json: {e}")
    finally:
        # Cancellation/shutdown must not leave a runtime busy flag behind.
        for work in session.pending_chat_work:
            session.active_turn_ids.discard(work.turn_id)
            session.pipeline_finishes += 1
        session.pending_chat_work.clear()
        session.chat_worker_running = False


@app.post("/api/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    changed = []
    turn_id = None
    input_message = req.message

    if req.message:
        turn = global_session.begin_chat_turn(req.message)
        turn_id = turn.id
        changed.append("/dev/stdin")

    elif req.reroll:
        if global_session.chat_busy:
            return JSONResponse(
                status_code=409, content={"error": "A chat turn is still running"}
            )

        turn = global_session.last_chat_turn()
        if turn is not None:
            # Remove every product of the old attempt, then replay the original
            # user message through the same turn identity.
            rollback_turn(turn.id)
            turn_id = turn.id
            input_message = turn.user_message
            changed.append("/dev/stdin")
        else:
            # Compatibility for histories created before turn provenance existed.
            from sxpb.types import SxpbMany

            history_str = global_session.artifacts.get(CHAT_HISTORY_ARTIFACT, "")
            if history_str:
                try:
                    parsed = sxpb.loads(history_str, precise=True)
                    history_list: MutableSequence = []
                    if isinstance(parsed, MutableMapping):
                        history_list_raw = cast(dict, parsed).get("history", [])
                        if isinstance(history_list_raw, MutableSequence):
                            history_list = history_list_raw

                    if history_list:
                        history_list = list(history_list)
                        user_role = get_user_role_name().lower()
                        if list(history_list[-1].keys())[0].lower() != user_role:
                            history_list.pop()
                            new_history_str = sxpb.dumps(
                                {"history": SxpbMany(history_list)}
                            )
                            global_session.save_artifact(
                                CHAT_HISTORY_ARTIFACT,
                                new_history_str,
                                source="legacy",
                            )
                        changed.append(CHAT_HISTORY_ARTIFACT)
                except Exception as e:
                    print(f"Reroll pop failed: {e}")

    if changed:
        try:
            persist_state()
        except Exception as e:
            if req.message:
                global_session.chat_turns.pop()
            print(f"Failed to save artifacts.json: {e}")
            return JSONResponse(
                status_code=500, content={"error": "Failed to save state"}
            )

        global_session.pipeline_triggers += 1
        trigger_gen = global_session.pipeline_triggers
        if turn_id is not None:
            global_session.active_turn_ids.add(turn_id)
        global_session.pending_chat_work.append(
            ChatWork(changed, turn_id, input_message)
        )
        if not global_session.chat_worker_running:
            global_session.chat_worker_running = True
            background_tasks.add_task(run_chat_queue)
        return {
            "status": "ok",
            "trigger_gen": trigger_gen,
            "turn_id": turn_id,
        }

    return {"status": "ok"}


# Serve static files
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")


@app.exception_handler(404)
async def spa_fallback(request, exc):
    index_path = DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"error": "Not Found"})


def main():
    port = int(os.getenv("PALUDORO_PORT", config.get("server", {}).get("port", 8000)))
    cfg = uvicorn.Config(
        "paludoro.server:app", host="0.0.0.0", port=port, reload=False, log_level="info"
    )
    server = uvicorn.Server(cfg)

    def handle_exit(sig, frame):
        server.should_exit = True
        if not server.started:
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    try:
        server.run()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
