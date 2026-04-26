from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI(title="Paludoro")

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DIST_DIR = BASE_DIR / "dist"


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": "paludoro"}


# Serve static files from the dist directory
# We mount this at the end so it doesn't shadow our API
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
else:

    @app.get("/")
    async def root_fallback():
        return {"message": "web/dist not found. Please run 'pdm run web-build'"}


# Optional: Catch-all for SPA routing (PWA)
@app.exception_handler(404)
async def spa_fallback(request, exc):
    index_path = DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Not Found"}
