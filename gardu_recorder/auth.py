"""Login satu password bersama: plain text di .env, cookie sesi bertanda tangan + sesi di SQLite, kunci per IP, CSRF."""
import hashlib
import hmac
import secrets
import threading
import time

import db
from config import settings
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from web import render_login

router = APIRouter()
PASSWORD = settings.GARDU_PASSWORD.encode()
MAX_FAIL, LOCK_SECONDS, SESSION_SECONDS = 5, 300, 12 * 3600
_fails: dict[str, list[float]] = {}     # satu proses uvicorn; reset saat restart
_fails_lock = threading.Lock()          # POST /login berjalan paralel di threadpool: cek+catat harus atomik
# Sidik password di cookie: ganti GARDU_PASSWORD → semua sesi lama otomatis tidak berlaku
PW_FINGERPRINT = hmac.new(settings.GARDU_SECRET.encode(), PASSWORD, hashlib.sha256).hexdigest()[:32]


def client_ip(request: Request) -> str:
    """IP browser. Di belakang reverse proxy, uvicorn mengambilnya dari X-Forwarded-For hanya bila IP proxy ada di
    FORWARDED_ALLOW_IPS (bawaan 127.0.0.1): tanpa itu semua petugas tampak satu IP dan berbagi kunci login."""
    return request.client.host if request.client else "-"


INSECURE_HTTP = ("Gardu dibuka lewat http, padahal COOKIE_SECURE=true: browser menolak cookie sesi sehingga login "
                 "tidak bisa. Buka lewat https, atau set COOKIE_SECURE=false di .env (hanya untuk LAN tertutup).")


def insecure_http(request: Request) -> bool:
    """Cookie Secure tidak disimpan browser lewat http (localhost dikecualikan browser)."""
    return (settings.COOKIE_SECURE and request.url.scheme != "https"
            and request.url.hostname not in ("localhost", "127.0.0.1", "::1"))


def csrf_token(request: Request) -> str:
    """Token CSRF per sesi; dibuat saat pertama diminta (juga untuk halaman login)."""
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


async def verify_csrf(request: Request):
    """Semua non-GET wajib membawa token: header X-CSRF-Token (htmx) atau field csrf_token (form biasa)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("X-CSRF-Token")
    if sent is None and request.headers.get("content-type", "").startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")):
        sent = (await request.form()).get("csrf_token")
    expected = request.session.get("csrf", "")
    if not expected or not sent or not hmac.compare_digest(str(sent), expected):
        raise HTTPException(403, "Token keamanan tidak valid. Muat ulang halaman.")


def logged_in(request: Request) -> bool:
    s, now = request.session, time.time()
    if not s.get("ok") or s.get("exp", 0) < now or not s.get("sid"):
        return False
    return hmac.compare_digest(str(s.get("pw", "")), PW_FINGERPRINT) and db.session_valid(s["sid"], now)


def require_login(request: Request):
    if not logged_in(request):
        raise HTTPException(401)


# Dipasang pada router yang dilindungi: login dulu, lalu CSRF (urutan dependency = urutan deklarasi)
protected = [Depends(require_login), Depends(verify_csrf)]


def _recent_fails(ip: str, now: float) -> list[float]:
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    if recent:
        _fails[ip] = recent
    else:
        _fails.pop(ip, None)
    if len(_fails) > 1000:          # batasi memori bila diserang dari banyak IP
        for k in [k for k, v in _fails.items() if now - v[-1] >= LOCK_SECONDS]:
            del _fails[k]
    return recent


@router.get("/login")
def login_page(request: Request, salah: int = 0):
    if logged_in(request):
        return RedirectResponse(settings.HOME_PATH, status_code=303)
    error = INSECURE_HTTP if insecure_http(request) else "Password salah." if salah else ""
    return render_login(request, csrf_token(request), error=error)


@router.post("/login")
def login(request: Request, password: str = Form(..., max_length=256), csrf: str = Form("", alias="csrf_token")):
    ip, now = client_ip(request), time.time()
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(csrf, expected):
        raise HTTPException(403, INSECURE_HTTP if insecure_http(request) else
                            "Token keamanan tidak valid. Muat ulang halaman.")
    with _fails_lock:
        recent = _recent_fails(ip, now)
        if len(recent) >= MAX_FAIL:
            raise HTTPException(429, "Terlalu banyak percobaan, tunggu 5 menit")
        ok = hmac.compare_digest(password.encode(), PASSWORD)
        if ok:
            _fails.pop(ip, None)
        else:
            _fails[ip] = fails = recent + [now]
    if not ok:
        db.audit("login", ip, f"Login gagal ({len(fails)}×)")
        return RedirectResponse("/login?salah=1", status_code=303)
    request.session.clear()                                           # sesi baru: token CSRF baru
    sid = secrets.token_urlsafe(32)
    db.session_create(sid, now + SESSION_SECONDS, now)
    request.session.update(ok=True, exp=now + SESSION_SECONDS, sid=sid, pw=PW_FINGERPRINT)
    csrf_token(request)
    db.audit("login", ip, "Login berhasil")
    return RedirectResponse(settings.HOME_PATH, status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    if sid := request.session.get("sid"):
        db.session_delete(sid)                  # cookie lama (mis. salinan curian) ikut tidak berlaku
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
