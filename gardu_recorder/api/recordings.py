"""Menu Rekaman: pilih kamera + tanggal → 24 jam → 60 menit; player menyambung segmen di browser (rekaman.js).

Nama file = jam lokal server (ffmpeg -strftime di recorder), jadi "sekarang" juga memakai jam lokal server.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path as FsPath
from typing import Annotated

import db
from auth import protected
from config import storage
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import FileResponse, JSONResponse
from web import render

router = APIRouter(prefix="/recordings", dependencies=protected)

CAM_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"          # sama dengan ImportCamera.id
FILE_PATTERN = r"^\d{2}-\d{2}-\d{2}\.mp4$"
_FILE_RE = re.compile(FILE_PATTERN)
CamId = Annotated[str, Path(pattern=CAM_PATTERN)]
FileName = Annotated[str, Path(pattern=FILE_PATTERN)]


def _day_dir(cam: str, day: date) -> FsPath:
    root = storage.path.resolve()
    d = (root / cam / day.isoformat()).resolve()
    if not d.is_relative_to(root):                   # pola cam/tanggal sudah mencegah '..'; ini jaga symlink
        raise HTTPException(400, "Path tidak valid.")
    return d


def segments(cam: str, day: date) -> list[dict]:
    """Segmen satu hari, terurut. File 0 byte (baru dibuka ffmpeg) dilewati."""
    d = _day_dir(cam, day)
    out = []
    for f in sorted(d.glob("*.mp4")) if d.is_dir() else []:
        if not _FILE_RE.match(f.name):
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size:
            out.append({"file": f.name, "t": f.name[:5].replace("-", ":"), "size": size})
    return out


def camera_choices() -> list[dict]:
    """Kamera di database, lalu folder rekaman kamera yang sudah dihapus (rekamannya tetap sampai lewat retensi)."""
    cams = [{"id": c.id, "name": c.name} for c in db.list_cameras()]
    known = {c["id"] for c in cams}
    root = storage.path
    if root.is_dir():
        for d in sorted(root.iterdir()):
            # folder hari (termasuk "besok" yang dibuat recorder) bisa tersisa kosong setelah retensi
            if (d.is_dir() and d.name not in known and re.match(CAM_PATTERN, d.name)
                    and next(d.glob("*/*.mp4"), None)):
                cams.append({"id": d.name, "name": f"{d.name} (dihapus)"})
    return cams


def _limit(day: date, segs: list[dict], online: bool) -> int:
    """Menit terakhir (0–1439) yang boleh dipilih; -1 = belum ada sama sekali."""
    today = date.today()
    if day > today:
        return -1
    if day < today:
        return 24 * 60 - 1
    now = datetime.now()
    limit = now.hour * 60 + now.minute
    if not online:                                   # kamera offline: jam setelah rekaman terakhir dinonaktifkan
        limit = min(limit, _minute(segs[-1]["t"])) if segs else -1
    return limit


def _minute(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:5])


def timeline(day: date, segs: list[dict], online: bool, jam: int | None) -> dict:
    recorded = {_minute(s["t"]) for s in segs}
    limit = _limit(day, segs, online)
    if jam is None:
        jam = _minute(segs[-1]["t"]) // 60 if segs else max(limit, 0) // 60
    jam = min(max(jam, 0), 23)
    hours = [{"h": h, "n": sum(1 for m in range(h * 60, h * 60 + 60) if m in recorded), "dis": h * 60 > limit}
             for h in range(24)]
    minutes = []
    for i in range(60):
        m = jam * 60 + i
        state = "ada" if m in recorded else "nanti" if m > limit else "kosong"
        minutes.append({"t": f"{jam:02d}:{i:02d}", "state": state})
    return {"jam": jam, "hours": hours, "minutes": minutes, "recorded": len(recorded)}


@router.get("")
def index(request: Request, cam: str = "", tgl: date | None = None, jam: int | None = None):
    cams = camera_choices()
    today = date.today()
    day = tgl or today
    min_day = today - timedelta(days=storage.retention_days - 1)     # hari lebih tua sudah dihapus cleaner
    prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    ctx = {"cams": cams, "cam": None, "day": day, "today": today, "min_day": min_day,
           "prev_day": prev_day if prev_day >= min_day else None, "next_day": next_day if next_day <= today else None}
    if cams:
        cam = cam or cams[0]["id"]
        selected = next((c for c in cams if c["id"] == cam), None)
        if selected is None:
            raise HTTPException(404, "Kamera tidak ditemukan.")
        rec = request.app.state.manager.recs.get(cam)
        segs = segments(cam, day)
        ctx.update(cam=selected, **timeline(day, segs, bool(rec and rec.online), jam))
    return render(request, "recordings/index.html", "recordings/_timeline.html", ctx)


@router.get("/{cam}/{day}")
def list_day(cam: CamId, day: date):
    return JSONResponse(segments(cam, day))


@router.get("/{cam}/{day}/{name}")
def get_file(cam: CamId, day: date, name: FileName, unduh: int = 0):
    f = _day_dir(cam, day) / name
    if not f.is_file():
        raise HTTPException(404, "Segmen tidak ditemukan.")
    # FileResponse mendukung Range (seek di browser) dan ETag; no-cache: segmen yang sedang ditulis masih tumbuh
    return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "private, no-cache"},
                        filename=f"{cam}_{day.isoformat()}_{name}" if unduh else None,
                        content_disposition_type="attachment")
