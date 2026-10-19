"""Rendering HTML: Jinja2, fragment HTMX, static ber-hash, security headers, error handler sadar-HTMX."""
import functools
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import settings
from fastapi import Request
from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
_tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _common_context(request: Request) -> dict:
    s = request.scope.get("session") or {}
    # logged_in hanya untuk tampilan (menu, Keluar); akses tetap dijaga auth.require_login. Token CSRF sudah ada sejak
    # halaman login dibuka, jadi bukan tanda sudah login.
    return {"csrf_token": s.get("csrf", ""), "nav_path": request.url.path, "logged_in": bool(s.get("ok"))}


def _file_version(path: str) -> str:
    try:
        return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12]
    except FileNotFoundError:
        return "missing"


_cached_file_version = functools.cache(_file_version)


def static_url(path: str) -> str:
    """URL static ber-?v=<hash isi>: deploy baru langsung terambil, dan cache boleh panjang."""
    version = _file_version(path) if settings.DEBUG else _cached_file_version(path)
    return f"/static/{path}?v={version}"


def _to_local(value: str | datetime | None) -> datetime | None:
    """Waktu di DB = UTC ('YYYY-MM-DD HH:MM:SS') → zona DISPLAY_TIMEZONE."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return value.replace(tzinfo=UTC).astimezone(_tz)


def localtime(value, fmt: str = "%d-%m-%Y %H:%M") -> str:
    dt = _to_local(value)
    return dt.strftime(fmt) if dt else ""


def logtime(value) -> str:
    """Gaya log: '07:58', 'Kemarin 22:10', '17 Okt 09:30'."""
    dt = _to_local(value)
    if not dt:
        return ""
    today = datetime.now(_tz).date()
    hm = dt.strftime("%H:%M")
    if dt.date() == today:
        return hm
    if dt.date() == today - timedelta(days=1):
        return f"Kemarin {hm}"
    return f"{dt.day} {_BULAN[dt.month - 1]} {hm}"


def tanggal(d) -> str:
    return f"{d.day} {_BULAN[d.month - 1]}"


def clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, _tz).strftime("%H:%M:%S") if epoch else "—"


def num(value: float, digits: int = 0) -> str:
    """Angka gaya Indonesia: 2.350 dan 3,1."""
    s = f"{value:,.{digits}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def filesize(n: int | None) -> str:
    """'412 MB', '1,4 GB'; satuan desimal seperti di pengelola file."""
    if n is None:
        return "—"
    if n < 1e6:
        return f"{num(n / 1e3)} KB"
    if n >= 1e9:
        return f"{num(n / 1e9, 1)} GB"
    return f"{num(n / 1e6, 1 if n < 1e7 else 0)} MB"


def duration(seconds: float) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} hari {h} jam"
    return f"{h} jam {m} menit" if h else f"{m} menit"


templates = Jinja2Templates(directory=WEB_DIR / "templates", context_processors=[_common_context])
templates.env.auto_reload = settings.DEBUG
templates.env.globals["static_url"] = static_url
templates.env.filters.update(localtime=localtime, logtime=logtime, tanggal=tanggal, clock=clock, num=num,
                             duration=duration, filesize=filesize)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def is_htmx_partial(request: Request) -> bool:
    # History restore (tombol back setelah cache miss) butuh full page
    return is_htmx(request) and request.headers.get("HX-History-Restore-Request") != "true"


def render(request: Request, page: str, fragment: str, context: dict, **kwargs):
    name = fragment if is_htmx_partial(request) else page
    response = templates.TemplateResponse(request=request, name=name, context=context, **kwargs)
    response.headers["Vary"] = "HX-Request"      # cegah fragment disajikan sebagai full page oleh cache
    return response


def render_login(request: Request, token: str, error: str = ""):
    return templates.TemplateResponse(request=request, name="login.html",
                                      context={"csrf_token": token, "error": error})
