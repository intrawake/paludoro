import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sxpb

from paludoro.chat import call_api
from paludoro.prompt import build_prompt
from paludoro.state import PaludoroSession, parse_assistant_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        # Shutdown
        print("\nPaludoro (FastAPI) shutting down...")


app = FastAPI(title="Paludoro", lifespan=lifespan)

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DIST_DIR = BASE_DIR / "dist"

# Config
config: dict = {}
try:
    loaded = sxpb.load(str(BASE_DIR / "preset" / "config.sxpb"))
    if isinstance(loaded, dict):
        config = loaded
except Exception:
    pass

MODEL = config.get("chat_model", {}).get("name", "openrouter/openrouter/free")
API_URL = config.get("chat_model", {}).get("api_url", "http://atomman-0-host:11435/v1")


class Message(BaseModel):
    role: str
    content: str
    raw_content: str = ""


class ChatRequest(BaseModel):
    history: list[Message]


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": "paludoro"}


@app.post("/api/files")
async def get_files(req: ChatRequest):
    session = PaludoroSession()
    for m in req.history:
        session.add_message(m.role, m.content, m.raw_content or m.content)
    session.rebuild_files()
    return {"files": session.files}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session = PaludoroSession()
    for m in req.history:
        session.add_message(m.role, m.content, m.raw_content or m.content)
    session.rebuild_files()

    prompt = build_prompt(session)

    # In a real app, we'd want this to be async or offloaded
    response_raw = await asyncio.to_thread(call_api, MODEL, prompt, API_URL)
    if not response_raw:
        return {"error": "Failed to get response from AI after multiple retries."}

    clean_resp, new_files = parse_assistant_response(response_raw)

    # Update state temporarily to return the final files
    session.add_message("assistant", clean_resp, raw_content=response_raw)
    for name, content in new_files.items():
        session.save_file(name, content)

    return {
        "message": {
            "role": "assistant",
            "content": clean_resp,
            "raw_content": response_raw,
        },
        "files": session.files,
    }


# Serve static files
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")


@app.exception_handler(404)
async def spa_fallback(request, exc):
    index_path = DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Not Found"}


def main():
    import uvicorn
    import signal
    import sys

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
