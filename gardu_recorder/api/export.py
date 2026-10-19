"""Menu Ekspor: rentang maks. 2 jam satu kamera → satu mp4 di latar (exporter.ExportQueue) + riwayat hari ini.

Jam di form = jam lokal server, sama dengan nama file segmen. Jam selesai ≤ jam mulai berarti melewati tengah malam.
"""
import asyncio
import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import db
import exporter
from auth import client_ip, protected
from config import settings, storage
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from web import render, templates

from api.recordings import camera_choices

router = APIRouter(prefix="/export", dependencies=protected)
FIELDS = ("cam", "tgl", "mulai", "selesai", "pemohon")


def _values(src) -> dict:
    """Nilai form. Jam dipilih lewat dua dropdown (mulai_j + mulai_m); tautan (Ulangi, Rekaman) memakai mulai=HH:MM."""
    v = {k: str(src.get(k, ""))[:200] for k in FIELDS}
    for k in ("mulai", "selesai"):
        if src.get(f"{k}_j") is not None:
            v[k] = f"{src.get(f'{k}_j')}:{src.get(f'{k}_m', '00')}"[:200]
    return v
_tz = ZoneInfo(settings.DISPLAY_TIMEZONE)


def _defaults(cams: list[dict], query) -> dict:
    """Default: 30 menit terakhir. Query (tombol Ulangi, tautan dari Rekaman) menimpa per kolom."""
    end = datetime.now().replace(second=0, microsecond=0)
    start = end - timedelta(minutes=30)
    v = {"cam": cams[0]["id"] if cams else "", "tgl": start.date().isoformat(),
         "mulai": f"{start:%H:%M}", "selesai": f"{end:%H:%M}", "pemohon": ""}
    v.update({k: x[:80] for k, x in _values(query).items() if x})
    return v


_JAM_RE = re.compile(r"^([01]?\d|2[0-3])[:.]?([0-5]\d)$")


def _jam(s: str) -> time:
    """Jam 24 jam: '14:30', '14.30', '1430', '9:05'. ValueError bila bukan jam."""
    m = _JAM_RE.match(s.strip())
    if not m:
        raise ValueError(s)
    return time(int(m[1]), int(m[2]))


def _check(v: dict, cams: list[dict], *, submit: bool) -> tuple[list[str], dict | None]:
    """Validasi form. Hasil: (pesan galat, {cam, start, end, pemohon}); pemohon hanya wajib saat dikirim."""
    errors = []
    cam = next((c for c in cams if c["id"] == v["cam"]), None)
    if cam is None:
        errors.append("Pilih kamera.")
    start = end = None
    try:
        day = date.fromisoformat(v["tgl"])
        t0, t1 = _jam(v["mulai"]), _jam(v["selesai"])
    except ValueError:
        errors.append("Isi tanggal, jam mulai, dan jam selesai (format 24 jam, mis. 14:30).")
    else:
        v["mulai"], v["selesai"] = f"{t0:%H:%M}", f"{t1:%H:%M}"      # form ditampilkan ulang dalam bentuk baku
        start, end = datetime.combine(day, t0), datetime.combine(day, t1)
        if end <= start:
            end += timedelta(days=1)                 # melewati tengah malam
        today = date.today()
        if t0 == t1:
            errors.append("Jam selesai harus setelah jam mulai.")
        elif not today - timedelta(days=storage.retention_days - 1) <= day <= today:   # sama dengan cleaner
            errors.append(f"Tanggal di luar masa simpan rekaman ({storage.retention_days} hari).")
        elif end - start > exporter.MAX_RANGE:
            errors.append("Rentang tidak valid (maksimal 2 jam).")
        elif end > datetime.now():
            errors.append("Jam selesai belum lewat.")
    pemohon = " ".join(v["pemohon"].split())
    if submit and not pemohon:
        errors.append("Nama pemohon wajib diisi (dicatat di log).")
    elif len(pemohon) > 80:
        errors.append("Nama pemohon maksimal 80 karakter.")
    if errors:
        return errors, None
    return [], {"cam": cam, "start": start, "end": end, "pemohon": pemohon}


def _summary(req: dict) -> dict:
    files = exporter.segments(storage.path, req["cam"]["id"], req["start"], req["end"], storage.segment_seconds)
    size = 0
    for f in files:
        try:
            size += f.stat().st_size
        except OSError:
            pass
    s, e = req["start"], req["end"]
    return {"minutes": int((e - s).total_seconds() // 60), "files": len(files), "size": size,
            "range": f"{s:%H:%M}–{e:%H:%M}" + (" (besok)" if e.date() > s.date() and e.time() != time() else ""),
            "filename": _filename(req["cam"]["id"], s, e)}


def _filename(cam: str, start: datetime, end: datetime) -> str:
    return f"{cam}_{start:%Y%m%d_%H%M}-{end:%H%M}.mp4"


def _form(request: Request, v: dict, cams: list[dict], errors=(), *, status_code=200, toast: str = "",
          headers=None):
    _, req = _check(v, cams, submit=False)
    return templates.TemplateResponse(
        request=request, name="export/_form.html", status_code=status_code, headers=headers,
        context={"v": v, "cams": cams, "errors": errors, "sum": _summary(req) if req else None, "toast": toast})


def _history(queue: exporter.ExportQueue) -> dict:
    midnight = datetime.now(_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for r in db.exports_since(midnight.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")):
        start, end = datetime.fromisoformat(r["mulai"]), datetime.fromisoformat(r["selesai"])
        q = {"cam": r["kamera"], "tgl": start.date().isoformat(), "mulai": f"{start:%H:%M}",
             "selesai": f"{end:%H:%M}", "pemohon": r["pemohon"]}
        rows.append({**dict(r), "range": exporter.range_label(start, end) if start.date() != date.today()
                     else f"{start:%H:%M}–{end:%H:%M}", "retry": urlencode(q),
                     # status tetap 'siap' setelah cleaner menghapus filenya (GARDU_EXPORT_KEEP_HOURS)
                     "file_ok": r["status"] == "siap" and queue.file(r["id"]).is_file()})
    return {"rows": rows, "running": any(r["status"] == "proses" for r in rows),
            "done": sum(1 for r in rows if r["status"] == "siap")}


@router.get("")
def index(request: Request):
    """Halaman Ekspor; permintaan HTMX (poll / setelah tambah) hanya mendapat tabel riwayat."""
    cams = camera_choices()
    v = _defaults(cams, request.query_params)
    _, req = _check(v, cams, submit=False)          # galat baru ditampilkan setelah "Tambah ke antrean"
    ctx = {"v": v, "cams": cams, "errors": [], "sum": _summary(req) if req else None,
           "keep_hours": settings.GARDU_EXPORT_KEEP_HOURS, **_history(request.app.state.exports)}
    return render(request, "export/index.html", "export/_history.html", ctx)


@router.get("/ringkasan")
def summary(request: Request):
    cams = camera_choices()
    v = _values(request.query_params)
    _, req = _check(v, cams, submit=False)          # tanpa galat: isian berubah = galat lama dihapus
    return templates.TemplateResponse(request=request, name="export/_summary.html",
                                      context={"errors": [], "sum": _summary(req) if req else None})


@router.post("")
async def create(request: Request):
    form = await request.form()
    v = _values(form)
    cams = await asyncio.to_thread(camera_choices)
    errors, req = _check(v, cams, submit=True)
    queue: exporter.ExportQueue = request.app.state.exports
    if not errors and queue.busy() >= exporter.MAX_JOBS:
        errors = [f"Antrean ekspor penuh ({exporter.MAX_JOBS}). Tunggu sampai ada yang selesai."]
    if errors:
        return _form(request, v, cams, errors, status_code=422)
    cam, start, end = req["cam"], req["start"], req["end"]
    ip = client_ip(request)
    export_id = await asyncio.to_thread(db.export_create, cam["id"], cam["name"], f"{start:%Y-%m-%d %H:%M}",
                                        f"{end:%Y-%m-%d %H:%M}", req["pemohon"], ip)
    queue.submit(exporter.Job(export_id, cam["id"], cam["name"], start, end, req["pemohon"], ip))
    toast = f"✔ Ekspor {cam['name']} {start:%H:%M}–{end:%H:%M} masuk antrean. Lihat Riwayat untuk mengunduh."
    return _form(request, v, cams, toast=toast, headers={"HX-Trigger": "export-added"})


@router.get("/{export_id}/download")
def download(request: Request, export_id: int):
    row = db.export_get(export_id)
    f = request.app.state.exports.file(export_id)
    if row is None or row["status"] != "siap" or not f.is_file():
        raise HTTPException(404, f"File ekspor tidak tersedia (dihapus otomatis setelah "
                                 f"{settings.GARDU_EXPORT_KEEP_HOURS} jam).")
    start, end = datetime.fromisoformat(row["mulai"]), datetime.fromisoformat(row["selesai"])
    return FileResponse(f, media_type="video/mp4", filename=_filename(row["kamera"], start, end),
                        headers={"Cache-Control": "private, no-store"})
