"""Menu Live View: grid 1/4/9 kamera, dan proxy HLS ke MediaMTX (hanya listen di 127.0.0.1) setelah login.

Video tidak membuka koneksi baru ke kamera: MediaMTX diisi salinan stream oleh recorder (ffmpeg live).
"""
import math
from typing import Annotated

import httpx
from auth import protected
from config import live
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from web import templates

from api.recordings import CamId

router = APIRouter(prefix="/live", dependencies=protected)

LAYOUTS = (1, 4, 9)
# index.m3u8, video1_stream.m3u8, <id>_init.mp4, <id>_seg12.mp4 … ; tanpa '/' dan tidak diawali '.'
HlsName = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")]
_PASS_HEADERS = ("content-type", "content-length", "content-encoding")     # aiter_raw: byte apa adanya


def _tile(rec) -> dict:
    return {"id": rec.cam.id, "name": rec.cam.name, "state": rec.state, "restarts": rec.restarts}


@router.get("")
def index(request: Request, n: int = 9, cam: str = "", hal: int = 1):
    recs = list(request.app.state.manager.recs.values())
    n = n if n in LAYOUTS else 9
    ctx = {"enabled": live.enabled, "n": n, "total": len(recs), "online": sum(r.state == "online" for r in recs),
           "tiles": [], "strip": [], "hal": 1, "pages": 1, "selected": None}
    if n == 1 and recs:
        selected = next((r for r in recs if r.cam.id == cam), None) or recs[0]
        ctx.update(selected=selected.cam.id, tiles=[_tile(selected)],
                   strip=[_tile(r) for r in recs if r is not selected])
    elif recs:
        pages = math.ceil(len(recs) / n)
        hal = min(max(hal, 1), pages)
        ctx.update(hal=hal, pages=pages, tiles=[_tile(r) for r in recs[(hal - 1) * n:hal * n]])
    return templates.TemplateResponse(request=request, name="live/index.html", context=ctx)


@router.get("/{cam}/{name}")
async def hls(request: Request, cam: CamId, name: HlsName):
    """Teruskan playlist/segmen HLS. Query string (mis. _HLS_msn) ikut diteruskan apa adanya."""
    if not live.enabled or cam not in request.app.state.manager.recs:
        raise HTTPException(404)
    client: httpx.AsyncClient = request.app.state.live_http
    req = client.build_request("GET", f"/{cam}/{name}", params=request.query_params)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        raise HTTPException(502, "Live view tidak tersedia: MediaMTX tidak menjawab.") from None
    headers = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_HEADERS}
    headers["Cache-Control"] = "no-store"
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                             background=BackgroundTask(upstream.aclose))
