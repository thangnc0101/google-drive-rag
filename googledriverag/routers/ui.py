from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from googledriverag.dependencies import verify_auth
from googledriverag.routers.system import _start_time

router = APIRouter(tags=["ui"])

_STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/", dependencies=[Depends(verify_auth)])
async def serve_ui(request: Request):
    index_path = _STATIC_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text()
        html = html.replace("__APP_VERSION__", str(int(_start_time)))
        config = request.app.state.config
        html = html.replace("__DEFAULT_MODE__", config.retrieval.default_mode)
        return HTMLResponse(html)
    return HTMLResponse("<h1>GoogleDriveRAG</h1><p>UI not available.</p>")
