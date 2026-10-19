"""Menu Kamera: daftar, tambah/ubah/hapus (SQLite), uji, snapshot dari segmen terbaru."""
import asyncio
import ipaddress
import json
import re
import sqlite3

import db
import tester
from auth import client_ip, protected
from config import ImportCamera, camera_ip_allowed, storage
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from web import render, templates

router = APIRouter(prefix="/cameras", dependencies=protected)
_snapshot_slots = asyncio.Semaphore(2)      # tiap snapshot = satu proses ffmpeg; batasi yang berjalan bersamaan

FIELD_LABELS = {"name": "Nama", "ip": "IP", "port": "Port", "device_id": "Device ID", "username": "Username",
                "password": "Password", "quality": "Kualitas", "fps": "FPS"}
CONNECTION_FIELDS = ("ip", "port", "device_id", "username", "password", "quality", "fps")
# Tujuan login: bila salah satu berubah, password tersimpan tidak boleh ikut dikirim (bisa dibaca penerimanya)
TARGET_FIELDS = ("ip", "port", "device_id", "username")
TARGET_CHANGED = "IP, port, Device ID, atau username diubah: isi ulang password kamera."


class CameraForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    ip: str = Field(min_length=1, max_length=45)
    port: int = Field(8800, ge=1, le=65535)
    device_id: int = Field(ge=1, le=2**32 - 1)      # dikemas "<I" oleh client V380
    username: str = Field(min_length=1, max_length=64)
    password: str = Field("", max_length=128)
    quality: int = Field(1, ge=0, le=1)
    fps: int = Field(16, ge=1, le=60)

    @field_validator("ip")
    @classmethod
    def _valid_ip(cls, v: str) -> str:
        ipaddress.ip_address(v)                      # ValueError → "bukan alamat IP yang valid"
        if not camera_ip_allowed(v):
            raise ValueError("di luar jaringan kamera")
        return v


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def free_id(base: str) -> str:
    """ID dari nama, diberi akhiran -2, -3, … bila sudah dipakai kamera lain atau folder rekaman (kamera yang dihapus):
    rekaman lama tidak ikut tampil sebagai milik kamera baru."""
    taken = {c.id for c in db.list_cameras()}
    cam_id, n = base, 1
    while cam_id in taken or (storage.path / cam_id).exists():
        n += 1
        suffix = f"-{n}"
        cam_id = base[:40 - len(suffix)].rstrip("-") + suffix
    return cam_id


def _messages(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors(include_context=False):
        label = FIELD_LABELS.get(str(e["loc"][0]), str(e["loc"][0]))
        kind = e["type"]
        if kind in ("missing", "string_too_short"):
            out.append(f"{label} wajib diisi.")
        elif kind.startswith("int_"):
            out.append(f"{label} harus berupa angka.")
        elif kind == "value_error" and "jaringan kamera" in e["msg"]:
            out.append(f"{label} di luar jaringan kamera yang diizinkan (GARDU_CAMERA_NETWORKS).")
        elif kind == "value_error":
            out.append(f"{label} bukan alamat IP yang valid.")
        else:
            out.append(f"{label} tidak valid.")
    return out


def _target_changed(data: CameraForm, saved: db.Camera) -> bool:
    return any(getattr(data, f) != getattr(saved, f) for f in TARGET_FIELDS)


def _form_values(form) -> dict:
    return {k: form.get(k, "") for k in FIELD_LABELS}


def _modal(request: Request, *, mode: str, values: dict, cam_id: str = "", errors=(), status_code=200):
    headers = {"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"} if status_code != 200 else {}
    return templates.TemplateResponse(
        request=request, name="cameras/_modal.html", status_code=status_code, headers=headers,
        context={"mode": mode, "v": values, "cam_id": cam_id, "errors": errors})


def _done(request: Request, message: str):
    """Sukses: kosongkan modal, tampilkan toast (OOB), dan minta tabel memuat ulang lewat event kebab-case."""
    return templates.TemplateResponse(
        request=request, name="components/_toast_oob.html", context={"message": message},
        headers={"HX-Trigger": json.dumps({"camera-saved": {}})})


def _table_ctx(request: Request, q: str, status: str) -> dict:
    manager = request.app.state.manager
    q = q.strip().lower()
    rows = []
    for rec in manager.recs.values():
        c = rec.cam
        if q and q not in f"{c.name} {c.id} {c.ip}".lower():
            continue
        if status in ("online", "connecting", "offline") and rec.state != status:
            continue
        rows.append(rec)
    return {"rows": rows, "total": len(manager.recs), "q": q, "status": status,
            "connecting": any(r.state == "connecting" for r in manager.recs.values())}


@router.get("")
def index(request: Request, q: str = "", status: str = "all"):
    return render(request, "cameras/index.html", "cameras/_table.html", _table_ctx(request, q, status))


@router.get("/table")
def table(request: Request, q: str = "", status: str = "all"):
    return templates.TemplateResponse(request=request, name="cameras/_table.html",
                                      context=_table_ctx(request, q, status))


@router.get("/new")
def new_form(request: Request):
    return _modal(request, mode="add", values={"username": "admin", "port": 8800, "quality": 1, "fps": 16})


@router.post("/test")
async def test_form(request: Request):
    """Uji dari form (kamera belum tersimpan, atau parameter diubah)."""
    form = await request.form()
    cam_id = str(form.get("cam_id", ""))
    saved = db.get_camera(cam_id) if cam_id else None
    try:
        data = CameraForm.model_validate(_form_values(form))
    except ValidationError as err:
        return templates.TemplateResponse(request=request, name="cameras/_test.html",
                                          context={"error": " ".join(_messages(err))})
    if not data.password and saved:
        if _target_changed(data, saved):
            return templates.TemplateResponse(request=request, name="cameras/_test.html",
                                              context={"error": TARGET_CHANGED})
        data = data.model_copy(update={"password": saved.password})
    if not data.password:
        return templates.TemplateResponse(request=request, name="cameras/_test.html",
                                          context={"error": "Isi password dulu untuk menguji."})
    rec = request.app.state.manager.recs.get(cam_id)
    if rec and (rec.cam.ip, rec.cam.port, rec.cam.device_id, rec.cam.username, rec.cam.password) != (
            data.ip, data.port, data.device_id, data.username, data.password):
        rec = None          # parameter koneksi diubah di form → uji login langsung dengan nilai baru
    mode, steps = await tester.run_test(data.ip, data.port, data.device_id, data.username, data.password,
                                        data.quality, rec)
    return templates.TemplateResponse(request=request, name="cameras/_test.html",
                                      context={"mode": mode, "steps": steps, "verdict": tester.verdict(steps)})


@router.post("")
async def create(request: Request):
    form = await request.form()
    values = _form_values(form)
    try:
        data = CameraForm.model_validate(values)
    except ValidationError as err:
        return _modal(request, mode="add", values=values, errors=_messages(err), status_code=422)
    errors = []
    if not data.password:
        errors.append("Password wajib diisi.")
    base_id = slugify(data.name)
    if not base_id:
        errors.append("Nama harus memuat huruf atau angka.")
    if errors:
        return _modal(request, mode="add", values=values, errors=errors, status_code=422)
    cam_id = await asyncio.to_thread(free_id, base_id)
    cam = ImportCamera(id=cam_id, **data.model_dump())
    try:
        await asyncio.to_thread(db.insert_camera, cam)
    except sqlite3.IntegrityError:
        return _modal(request, mode="add", values=values, errors=["IP sudah dipakai kamera lain."],
                      status_code=422)
    await asyncio.to_thread(db.audit, "kamera", client_ip(request), f"Tambah kamera {cam.name}")
    request.app.state.manager.start(db.get_camera(cam_id))
    return _done(request, f"✔ Kamera {cam.name} disimpan. Pipeline dimulai; menunggu data video pertama…")


def _get_or_404(cam_id: str) -> db.Camera:
    cam = db.get_camera(cam_id)
    if cam is None:
        raise HTTPException(404, "Kamera tidak ditemukan.")
    return cam


@router.get("/{cam_id}/edit")
def edit_form(request: Request, cam_id: str):
    cam = _get_or_404(cam_id)
    values = {k: getattr(cam, k) for k in FIELD_LABELS if k != "password"}
    return _modal(request, mode="edit", values=values, cam_id=cam.id)


@router.get("/{cam_id}/hapus")
def delete_form(request: Request, cam_id: str):
    cam = _get_or_404(cam_id)
    return templates.TemplateResponse(request=request, name="cameras/_delete.html", context={"cam": cam})


@router.put("/{cam_id}")
async def update(request: Request, cam_id: str):
    old = _get_or_404(cam_id)
    form = await request.form()
    values = _form_values(form)
    try:
        data = CameraForm.model_validate(values)
    except ValidationError as err:
        return _modal(request, mode="edit", values=values, cam_id=cam_id, errors=_messages(err), status_code=422)
    if not data.password and _target_changed(data, old):
        return _modal(request, mode="edit", values=values, cam_id=cam_id, errors=[TARGET_CHANGED], status_code=422)
    cam = ImportCamera(id=cam_id, **data.model_dump())
    try:
        await asyncio.to_thread(db.update_camera, cam_id, cam)    # password kosong = tidak diubah
    except sqlite3.IntegrityError:
        return _modal(request, mode="edit", values=values, cam_id=cam_id,
                      errors=["IP sudah dipakai kamera lain."], status_code=422)
    new = db.get_camera(cam_id)
    changed = [f for f in CONNECTION_FIELDS if getattr(new, f) != getattr(old, f)]
    detail = ", ".join(f"{f} {getattr(new, f)}" if f in ("ip", "fps") else f for f in changed)
    renamed = " (nama diubah)" if new.name != old.name and not changed else ""
    await asyncio.to_thread(db.audit, "kamera", client_ip(request),
                            f"Ubah kamera {new.name}" + (f" ({detail})" if detail else renamed))
    restarted = await request.app.state.manager.update(new, restart=bool(changed))
    note = " Pipeline kamera ini dimulai ulang; kamera lain tidak terganggu." if restarted else ""
    return _done(request, f"✔ Perubahan {new.name} disimpan.{note}")


@router.delete("/{cam_id}")
async def delete(request: Request, cam_id: str):
    cam = _get_or_404(cam_id)
    await request.app.state.manager.stop(cam_id)
    await asyncio.to_thread(db.delete_camera, cam_id)
    await asyncio.to_thread(db.audit, "kamera", client_ip(request), f"Hapus kamera {cam.name}")
    return _done(request, f"✔ Kamera {cam.name} dihapus. Pipeline dihentikan; rekaman lama tetap di NAS "
                          "sampai lewat retensi.")


@router.post("/{cam_id}/test")
async def test_saved(request: Request, cam_id: str):
    cam = _get_or_404(cam_id)
    rec = request.app.state.manager.recs.get(cam_id)
    mode, steps = await tester.run_test(cam.ip, cam.port, cam.device_id, cam.username, cam.password,
                                        cam.quality, rec)
    return templates.TemplateResponse(request=request, name="cameras/_test.html",
                                      context={"mode": mode, "steps": steps, "verdict": tester.verdict(steps)})


@router.get("/{cam_id}/snapshot")
async def snapshot(request: Request, cam_id: str):
    """JPEG dari segmen terbaru: tidak membuka koneksi baru ke kamera."""
    rec = request.app.state.manager.recs.get(cam_id)
    if rec is None:
        raise HTTPException(404)
    async with _snapshot_slots:
        for seg in rec.recent_segments(2):      # terbaru dulu; yang baru mulai ditulis bisa masih kosong
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-sseof", "-2", "-i", str(seg), "-frames:v", "1",
                "-vf", "scale=320:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), 10)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                continue
            if proc.returncode == 0 and out:
                return Response(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=30"})
    raise HTTPException(404)
