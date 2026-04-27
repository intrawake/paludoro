import asyncio
import os
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paludoro.chat import call_api
from paludoro.prompt import build_prompt
from paludoro.state import PaludoroSession, parse_assistant_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    print("\n🕵️‍♀️ Paludoro (FastAPI) shutting down...")


app = FastAPI(title="Paludoro", lifespan=lifespan)

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DIST_DIR = BASE_DIR / "dist"

# Global State
session = PaludoroSession()
MODEL = "openrouter/openrouter/free"
API_URL = "http://atomman-0-host:11435/v1"


class ChatRequest(BaseModel):
    message: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": "paludoro"}


@app.get("/api/files")
async def get_files():
    return session.files


@app.get("/api/history")
async def get_history():
    return [{"role": r, "content": c} for r, c, _ in session.history]


@app.post("/api/clear")
async def clear_session():
    session.reset()
    return {"status": "cleared"}


@app.post("/api/delete_turn")
async def delete_turn():
    session.delete_last_turn()
    return {"status": "deleted"}


@app.post("/api/reroll")
async def reroll():
    session.pop_assistant_message()
    if not session.history:
        return {"error": "No history to reroll"}

    # Use the same prompt building logic
    prompt = build_prompt(session)
    response_raw = await asyncio.to_thread(call_api, MODEL, prompt, API_URL)
    if not response_raw:
        return {"error": "Failed to get response from AI after multiple retries."}

    clean_resp, new_files = parse_assistant_response(response_raw)

    session.add_message("assistant", clean_resp, raw_content=response_raw)
    for name, content in new_files.items():
        session.save_file(name, content)

    return {"response": clean_resp, "new_files": list(new_files.keys())}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    user_msg = req.message
    session.add_message("user", user_msg)

    prompt = build_prompt(session)

    # In a real app, we'd want this to be async or offloaded
    response_raw = await asyncio.to_thread(call_api, MODEL, prompt, API_URL)
    if not response_raw:
        return {"error": "Failed to get response from AI after multiple retries."}

    clean_resp, new_files = parse_assistant_response(response_raw)

    # Update state
    session.add_message("assistant", clean_resp, raw_content=response_raw)
    for name, content in new_files.items():
        session.save_file(name, content)

    return {"response": clean_resp, "new_files": list(new_files.keys())}


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

    port = int(os.getenv("PALUDORO_PORT", "8000"))

    # GRACEFUL BUT COMPLETE SHUTDOWN
    def handle_sigint(sig, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        print(f"\n🕵️‍♀️ Paludoro (PID {os.getpid()}) exiting...")
        # Send SIGTERM to the process group (including PDM)
        try:
            os.killpg(0, signal.SIGTERM)
        except Exception:
            pass
        # Exit immediately to release the terminal
        os._exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    uvicorn.run(
        "paludoro.server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_dirs=[str(BASE_DIR / "src")],
    )


if __name__ == "__main__":
    main()
