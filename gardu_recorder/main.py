import asyncio
import logging
from contextlib import asynccontextmanager

import auth
import cleaner
import db
import exporter
import httpx
from api import cameras, dashboard, export, log, recordings
from api import live as live_view
from config import import_cameras, live, settings, storage
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from recorder import RecorderManager
from starlette.middleware.sessions import SessionMiddleware
from web import STATIC_DIR
from web.security import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
    install_error_handlers,
)


class Static(StaticFiles):
    """URL static ber-?v=<hash> (static_url): cache panjang aman; mode DEBUG tanpa cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = (
            "no-cache" if settings.DEBUG else "public, max-age=31536000, immutable")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_db()
    db.check_key()
    if db.count_cameras() == 0:      # impor awal sekali: sesudahnya database adalah sumber data
        for cam in import_cameras(settings.GARDU_CAMERAS):
            try:
                db.insert_camera(cam)
            except Exception:
                logging.getLogger("gardu").exception("impor kamera %s gagal", cam.id)
    # Proxy HLS ke MediaMTX (lokal): connect cepat gagal → 502; read cukup longgar untuk segmen besar.
    # Bearer = hlsCDNSecret: tanpa itu MediaMTX ≥1.21 membuat sesi per penonton lewat redirect cookieCheck.
    app.state.live_http = httpx.AsyncClient(base_url=live.hls_url, timeout=httpx.Timeout(15, connect=2),
                                            headers={"Authorization": f"Bearer {live.hls_secret}"})
    db.exports_abort_running("terhenti: aplikasi dimulai ulang")
    exports_dir = settings.GARDU_DATA_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    for f in exports_dir.glob("*.part.mp4*"):         # sisa ekspor yang terputus
        f.unlink(missing_ok=True)
    app.state.exports = exporter.ExportQueue(storage.path, storage.segment_seconds, exports_dir)
    manager = RecorderManager(storage, live)
    app.state.manager = manager
    manager.start_all(db.list_cameras())
    cleaner_task = asyncio.create_task(
        cleaner.run_cleaner(storage, exports_dir, settings.GARDU_EXPORT_KEEP_HOURS), name="cleaner")
    try:
        yield
    finally:
        cleaner_task.cancel()
        await app.state.exports.stop()
        await manager.stop_all()
        await app.state.live_http.aclose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(UnhandledErrorMiddleware)       # paling dalam: 500 tetap membawa security headers
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=settings.GARDU_SECRET, max_age=auth.SESSION_SECONDS,
                       same_site="strict", https_only=settings.COOKIE_SECURE, session_cookie="gardu_session")
    install_error_handlers(app)

    app.include_router(auth.router)
    app.include_router(dashboard.public)
    for r in (dashboard.router, live_view.router, recordings.router, export.router, cameras.router, log.router):
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse(settings.HOME_PATH, status_code=303)

    app.mount("/static", Static(directory=STATIC_DIR), name="static")
    return app


app = create_app()
