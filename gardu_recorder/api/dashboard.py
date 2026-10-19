"""Dashboard: KPI, tiga kartu ringkasan, segar otomatis (dirender server: tombol/poller mengganti dirinya sendiri)."""

import db
import stats
from auth import protected
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import render, templates

router = APIRouter(dependencies=protected)
public = APIRouter()


def _ctx(request: Request, auto: int) -> dict:
    return {"s": stats.collect(request.app.state.manager), "auto": bool(auto)}


@router.get("/dashboard")
def dashboard(request: Request, auto: int = 1):
    ctx = {**_ctx(request, auto), "log_kind": "ekspor", "log_rows": db.recent_logs("ekspor")}
    return render(request, "dashboard/index.html", "dashboard/_stats.html", ctx)


@router.get("/dashboard/stats")
def dashboard_stats(request: Request, auto: int = 1):
    return templates.TemplateResponse(request=request, name="dashboard/_stats.html", context=_ctx(request, auto))


@public.get("/health")
def health():
    """Tanpa login: hanya untuk healthcheck Docker/monitor, jadi tidak membocorkan apa pun (jumlah kamera, uptime)."""
    return JSONResponse({"status": "ok"})
