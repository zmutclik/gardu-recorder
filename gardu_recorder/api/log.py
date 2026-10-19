"""GET /log/{jenis}: 50 baris audit_log terbaru (kartu Log di Dashboard: ekspor, login, kamera)."""
import db
from auth import protected
from fastapi import APIRouter, HTTPException, Request
from web import templates

router = APIRouter(prefix="/log", dependencies=protected)


@router.get("/{jenis}")
def log(request: Request, jenis: str):
    if jenis not in db.LOG_KINDS:
        raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="dashboard/_log.html",
                                      context={"log_kind": jenis, "log_rows": db.recent_logs(jenis)})
