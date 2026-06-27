from collections.abc import MutableMapping, MutableSequence
import asyncio
import base64
import json
import logging
import os
import signal
import sys
import time
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
from paludoro.state import PaludoroSession
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

                for artipath, versions in global_session.artifact_versions.items():
                    new_versions = []
                    for content, turn in versions:
                        new_content = image_save_hook(artipath, content)
                        new_versions.append((new_content, turn))
                    global_session.artifact_versions[artipath] = new_versions
            else:
                # Old format (flat dict of artifacts)
                for k, v in stored.items():
                    global_session.save_artifact(k, v)
        except Exception as e:
            print(f"Failed to load artifacts.json: {e}")
    else:
        for k, v in default_artifacts.items():
            global_session.save_artifact(k, v)

    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        # Shutdown
        print("\nPaludoro (FastAPI) shutting down...")
        try:
            ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
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
            parsed.get("history", []) if isinstance(parsed, MutableMapping) else []  # type: ignore
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
        "running_agents": list(global_session.running_agents),
        "pipeline_triggers": global_session.pipeline_triggers,
        "pipeline_finishes": global_session.pipeline_finishes,
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
    if not req.history:
        global_session.reset()

    from sxpb.types import SxpbMany

    history_list = SxpbMany([])
    for msg in req.history:
        history_list.append({msg.role: msg.content})

    new_history_data = {"history": history_list}
    new_history_str = sxpb.dumps(new_history_data)

    global_session.save_artifact(CHAT_HISTORY_ARTIFACT, new_history_str)

    try:
        ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")

    return {"status": "ok"}


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


@app.get("/api/artifacts")
async def list_artifacts():
    """Return current artifact content and version counts."""
    version_counts = {}
    for artipath, versions in global_session.artifact_versions.items():
        version_counts[artipath] = len(versions)

    return {"artifacts": global_session.artifacts, "version_counts": version_counts}


@app.get("/api/artifacts/{artipath:path}/versions")
async def get_artifact_versions(artipath: str):
    """Return full version history for a single artifact (lazy load)."""
    versions = global_session.artifact_versions.get(artipath, [])
    if not versions and artipath not in global_session.artifacts:
        return JSONResponse(status_code=404, content={"error": "Artifact not found"})
    return {"artipath": artipath, "versions": versions}


@app.put("/api/artifacts/{artipath:path}")
async def update_artifact(artipath: str, req: ArtifactUpdateRequest):
    """Save edited content as a new version of the artifact."""
    global_session.save_artifact(artipath, req.content)
    try:
        ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")
    return {"status": "ok"}


@app.delete("/api/artifacts/{artipath:path}/versions/{version_index:int}")
async def delete_artifact_version(artipath: str, version_index: int):
    """Delete a specific version of an artifact. Cannot delete the last remaining version."""
    versions = global_session.artifact_versions.get(artipath, [])
    if not versions or version_index < 0 or version_index >= len(versions):
        return JSONResponse(status_code=404, content={"error": "Version not found"})
    if len(versions) <= 1:
        return JSONResponse(
            status_code=400, content={"error": "Cannot delete the last version"}
        )

    versions.pop(version_index)

    # Update current artifact content to the latest remaining version
    global_session.artifacts[artipath] = versions[-1][0]
    global_session.dirty_artifacts.add(artipath)

    try:
        ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
    except Exception as e:
        print(f"Failed to save artifacts.json: {e}")

    return {
        "status": "ok",
        "artifact_versions": global_session.artifact_versions,
    }


@app.get("/api/agents")
async def get_agents():
    return {"agents": list(config.get("agent_dict", {}).keys())}


@app.get("/api/llm-requests")
async def get_llm_requests():
    return {"requests": get_llm_request_log()}


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
                ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
            except Exception as e:
                print(f"Failed to save artifacts.json: {e}")
        except Exception as e:
            print(f"Manual agent run failed: {e}")
        finally:
            global_session.pipeline_finishes += 1

    background_tasks.add_task(run_agent_bg)
    return {"status": "ok", "trigger_gen": trigger_gen}


@app.post("/api/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    changed = []
    if req.message:
        global_session.save_artifact("/dev/stdin", req.message)
        changed.append("/dev/stdin")
    elif req.reroll:
        from sxpb.types import SxpbMany

        history_str = global_session.artifacts.get(CHAT_HISTORY_ARTIFACT, "")
        if history_str:
            try:
                from typing import cast

                parsed = sxpb.loads(history_str, precise=True)
                history_list: MutableSequence = []
                if isinstance(parsed, MutableMapping):
                    # Cast to dict for simpler Ty interaction
                    parsed_dict = cast(dict, parsed)
                    history_list_raw = parsed_dict.get("history", [])
                    if isinstance(history_list_raw, MutableSequence):
                        history_list = history_list_raw

                # Robust check for SxpbMany or list
                is_sxpb_many = (
                    hasattr(history_list, "__class__")
                    and history_list.__class__.__name__ == "SxpbMany"
                )

                if history_list and (
                    isinstance(history_list, (list, MutableSequence)) or is_sxpb_many
                ):
                    # Convert to list for easy manipulation if it's SxpbMany
                    if is_sxpb_many:
                        history_list = list(history_list)

                    user_role = get_user_role_name().lower()
                    if (
                        history_list
                        and list(history_list[-1].keys())[0].lower() != user_role
                    ):
                        history_list.pop()
                        # Wrap back in SxpbMany for the dump hint (())
                        new_history_data = {"history": SxpbMany(history_list)}
                        new_history_str = sxpb.dumps(new_history_data)
                        global_session.save_artifact(
                            CHAT_HISTORY_ARTIFACT, new_history_str
                        )

                    # Always trigger pipeline — even if nothing was popped,
                    # the user wants a (re)generation (e.g. retry after failure)
                    changed.append(CHAT_HISTORY_ARTIFACT)
            except Exception as e:
                print(f"Reroll pop failed: {e}")
    if changed:
        global_session.pipeline_triggers += 1
        trigger_gen = global_session.pipeline_triggers

        async def run_pipeline_bg(changed_arts):
            try:
                pipeline = AgentPipeline(config, global_session)

                await pipeline.on_artifacts_changed(changed_arts)

                try:
                    ARTIFACTS_FILE.write_text(json.dumps(global_session.to_dict()))
                except Exception as e:
                    print(f"Failed to save artifacts.json: {e}")
            except Exception as e:
                print(f"Agent pipeline failed: {e}")
            finally:
                global_session.pipeline_finishes += 1

        background_tasks.add_task(run_pipeline_bg, changed)
        return {"status": "ok", "trigger_gen": trigger_gen}

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
