"""Security headers + Cache-Control default (pure ASGI), dan error handler sadar-HTMX."""
import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from web import is_htmx, templates

logger = logging.getLogger(__name__)

# Tanpa 'unsafe-inline'/'unsafe-eval': tidak ada <script>/style inline; htmx allowEval=false.
# media-src: pemutar rekaman/live (tahap berikut) memakai blob/URL sendiri.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
]


class AutoSecureSessionMiddleware:
    """SessionMiddleware dengan cookie Secure hanya untuk request https (scheme dari uvicorn, termasuk
    X-Forwarded-Proto proxy yang dipercaya): login jalan lewat http di LAN maupun lewat reverse proxy TLS."""

    def __init__(self, app, **kwargs):
        self.plain = SessionMiddleware(app, https_only=False, **kwargs)
        self.secure = SessionMiddleware(app, https_only=True, **kwargs)

    async def __call__(self, scope, receive, send):
        mw = self.secure if scope.get("scheme") in ("https", "wss") else self.plain
        await mw(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [*message.get("headers", []), *SECURITY_HEADERS]
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))   # halaman berisi data rekaman/kamera
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


MESSAGES = {
    403: "Anda tidak punya akses.",
    404: "Halaman tidak ditemukan.",
    422: "Parameter permintaan tidak valid.",
    429: "Terlalu banyak percobaan. Coba lagi nanti.",
    500: "Terjadi kesalahan di server.",
}


def error_response(request: Request, status_code: int, detail: str | None = None) -> Response:
    message = MESSAGES.get(status_code) if detail in (None, "") or status_code >= 500 else detail
    message = message or "Permintaan tidak dapat diproses."
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": message}, status_code=status_code)
    if is_htmx(request):
        return templates.TemplateResponse(
            request=request, name="components/_toast.html", context={"message": message},
            status_code=status_code, headers={"HX-Retarget": "#toast", "HX-Reswap": "innerHTML"})
    return templates.TemplateResponse(
        request=request, name="error.html", context={"message": message, "status_code": status_code},
        status_code=status_code)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 401 and not request.url.path.startswith("/api/"):
            if is_htmx(request):
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login", status_code=303)
        return error_response(request, exc.status_code, str(exc.detail) if exc.detail else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith("/api/"):
            return await request_validation_exception_handler(request, exc)
        return error_response(request, 422)


class UnhandledErrorMiddleware:
    """Pure ASGI, dipasang PALING DALAM: exception tak tertangani jadi 500 di dalam middleware security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def send_tracking(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception:
            request = Request(scope)
            logger.exception("unhandled_error", extra={"path": request.url.path})
            if started:
                raise
            await error_response(request, 500)(scope, receive, send)
