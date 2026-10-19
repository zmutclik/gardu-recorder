"""Pipeline per kamera: client V380 (H.265 mentah di stdout) → ffmpeg perekam (segmen mp4) [+ ffmpeg live]."""
import asyncio
import logging
import os
import sys
import time
import urllib.parse
from collections import deque
from datetime import date, timedelta
from pathlib import Path

from config import LiveConfig, StorageConfig
from db import Camera

log = logging.getLogger("gardu.recorder")
V380 = Path(__file__).parent / "vendor" / "v380_stream.py"
STALL_SECONDS = 15
CONNECTING_GRACE = 30        # detik setelah pipeline pertama dimulai yang masih dianggap "menghubungkan"
BITRATE_WINDOW = 5.0
LIVE_RETRY = 10              # detik antar percobaan menyalakan ulang ffmpeg live (MediaMTX mati/restart)
LIVE_MAX_BUFFER = 8_000_000  # byte antre ke ffmpeg live sebelum dianggap tertinggal


class CameraRecorder:
    # Perintah sumber; diganti di test dengan sumber palsu
    source_cmd: list[str] = [sys.executable, str(V380)]

    def __init__(self, cam: Camera, storage: StorageConfig, live: LiveConfig):
        self.cam, self.live = cam, live
        self.root = Path(storage.path) / cam.id
        self.segment = storage.segment_seconds
        self.online = False
        self.restarts = 0
        self.bytes_total = 0
        self.last_data = 0.0                  # epoch detik data video terakhir
        self.mbps = 0.0
        self.started_at = time.time()
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self._win_t0, self._win_bytes = time.time(), 0
        self._live_retry_at = 0.0

    # ---- status untuk UI
    @property
    def state(self) -> str:
        if self.online:
            return "online"
        if self.restarts == 0 and time.time() - self.started_at < CONNECTING_GRACE:
            return "connecting"
        return "offline"

    def recent_segments(self, n: int) -> list[Path]:
        """Segmen terbaru dulu (nama file HH-MM-SS terurut; hari ini, lalu kemarin)."""
        out: list[Path] = []
        for d in (date.today(), date.today() - timedelta(days=1)):
            out += sorted((self.root / d.isoformat()).glob("*.mp4"), reverse=True)
            if len(out) >= n:
                break
        return out[:n]

    def latest_segment(self) -> Path | None:
        segs = self.recent_segments(1)
        return segs[0] if segs else None

    # ---- pipeline
    def _ensure_dirs(self):
        for d in (date.today(), date.today() + timedelta(days=1)):
            (self.root / d.isoformat()).mkdir(parents=True, exist_ok=True)

    def _v380_env(self) -> dict:
        # Sengaja bukan **os.environ: rahasia gardu (GARDU_*) tidak perlu sampai ke proses client V380
        c = self.cam
        return {"PATH": os.environ.get("PATH", ""),
                "V380_IP": c.ip, "V380_PORT": str(c.port), "V380_DEVICE_ID": str(c.device_id),
                "V380_USERNAME": c.username, "V380_PASSWORD": c.password,
                "V380_QUALITY": str(c.quality), "V380_OUTPUT": "-"}

    def _rec_cmd(self) -> list[str]:
        out = str(self.root / "%Y-%m-%d" / "%H-%M-%S.mp4")
        return ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "hevc", "-framerate", str(self.cam.fps),
                "-fflags", "+genpts", "-i", "pipe:0",
                "-map", "0:v", "-c:v", "copy", "-bsf:v", "setts=pts=DTS", "-tag:v", "hvc1",   # lihat _live_cmd
                "-f", "segment", "-segment_time", str(self.segment),
                "-segment_atclocktime", "1", "-reset_timestamps", "1",
                "-strftime", "1", "-segment_format", "mp4",
                "-segment_format_options",
                "movflags=+frag_keyframe+empty_moov+default_base_moof", out]

    def _live_url(self) -> str:
        """URL publish RTSP dengan kredensial (MediaMTX menolak publish tanpa user/password)."""
        url = f"{self.live.mediamtx_url}/{self.cam.id}"
        if not self.live.publish_password:
            return url
        scheme, rest = url.split("://", 1)
        user, pw = (urllib.parse.quote(v, safe="") for v in (self.live.publish_user, self.live.publish_password))
        return f"{scheme}://{user}:{pw}@{rest}"

    def _live_cmd(self) -> list[str]:
        return ["ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "hevc", "-framerate", str(self.cam.fps),
                "-fflags", "+genpts", "-i", "pipe:0", "-c:v", "copy",
                # demuxer hevc mentah hanya memberi DTS. Muxer mp4 masih mengisi PTS sendiri (dengan peringatan
                # "will stop working"), RTSP tidak: PTS jadi 0 dan MediaMTX tidak pernah memotong segmen HLS
                "-bsf:v", "setts=pts=DTS",
                "-f", "rtsp", "-rtsp_transport", "tcp", self._live_url()]

    async def run_forever(self):
        backoff = 2
        while True:
            started = time.time()
            try:                               # mkdir di dalam try: folder rekaman tanpa izin tulis tercatat di log,
                self._ensure_dirs()            # bukan mematikan task tanpa jejak
                await self._run_once()
            except Exception:
                log.exception("%s pipeline error", self.cam.id)
            self.online = False
            self.mbps = 0.0
            self.restarts += 1
            backoff = 2 if time.time() - started > 60 else min(backoff * 2, 60)
            log.warning("%s pipeline berhenti, ulang dalam %ss", self.cam.id, backoff)
            await asyncio.sleep(backoff)

    async def _drain_stderr(self, proc):
        while line := await proc.stderr.readline():
            self.stderr_tail.append(line.decode(errors="replace").rstrip())

    def _count(self, n: int):
        now = time.time()
        self.bytes_total += n
        self._win_bytes += n
        self.last_data = now
        if now - self._win_t0 >= BITRATE_WINDOW:
            self.mbps = self._win_bytes * 8 / (now - self._win_t0) / 1e6
            self._win_t0, self._win_bytes = now, 0

    async def _run_once(self):
        PIPE = asyncio.subprocess.PIPE
        src = rec = live = drain = None
        self._win_t0, self._win_bytes = time.time(), 0
        try:
            src = await asyncio.create_subprocess_exec(
                *self.source_cmd, stdout=PIPE, stderr=PIPE, env=self._v380_env())
            drain = asyncio.create_task(self._drain_stderr(src))
            rec = await asyncio.create_subprocess_exec(*self._rec_cmd(), stdin=PIPE)
            self._live_retry_at = 0.0
            while True:
                chunk = await asyncio.wait_for(src.stdout.read(65536), STALL_SECONDS)
                if not chunk:
                    break                      # client V380 keluar
                self.online = True
                self._count(len(chunk))
                rec.stdin.write(chunk)
                try:                           # rekaman tidak boleh hilang, tapi ffmpeg yang tidak menulis
                    await asyncio.wait_for(rec.stdin.drain(), STALL_SECONDS)    # (NAS hang) = pipeline macet
                except TimeoutError:
                    log.warning("%s ffmpeg perekam tidak menulis %ss (NAS lambat/macet?)", self.cam.id,
                                STALL_SECONDS)
                    break
                if self.live.enabled:
                    live = await self._feed_live(live, chunk)
        except TimeoutError:
            log.warning("%s tidak ada data %ss (macet)", self.cam.id, STALL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            log.warning("%s ffmpeg perekam mati", self.cam.id)
        finally:
            # shield: pembatalan task (hapus/ubah kamera, shutdown) tetap menutup segmen dengan rapi
            await asyncio.shield(self._teardown(src, rec, live, drain))

    async def _feed_live(self, live, chunk: bytes):
        """Live best-effort: write tanpa drain, jadi MediaMTX yang lambat tidak menahan rekaman. Publisher yang mati
        atau tertinggal dimatikan lalu dicoba lagi tiap LIVE_RETRY detik (ffmpeg baru menunggu VPS/SPS berikutnya)."""
        if live is not None:
            try:
                if live.returncode is None and live.stdin.transport.get_write_buffer_size() <= LIVE_MAX_BUFFER:
                    live.stdin.write(chunk)
                    return live
            except (BrokenPipeError, ConnectionResetError):
                pass
            log.warning("%s live view terputus, coba lagi dalam %ss", self.cam.id, LIVE_RETRY)
            await _stop(live)
            self._live_retry_at = time.time() + LIVE_RETRY
            return None
        if time.time() < self._live_retry_at:
            return None
        self._live_retry_at = time.time() + LIVE_RETRY
        try:
            live = await asyncio.create_subprocess_exec(*self._live_cmd(), stdin=asyncio.subprocess.PIPE)
        except OSError:
            log.exception("%s ffmpeg live gagal dijalankan", self.cam.id)
            return None
        live.stdin.write(chunk)
        return live

    async def _teardown(self, src, rec, live, drain):
        if src:
            await _stop(src)
        if drain:
            drain.cancel()
        if rec:
            await _stop(rec, graceful=True)    # tutup stdin → segmen ditutup rapi
        if live:
            await _stop(live, graceful=True)


async def _stop(p, graceful=False):
    if p.returncode is not None:
        return
    if graceful and p.stdin:
        p.stdin.close()
        try:
            await asyncio.wait_for(p.wait(), 10)
            return
        except TimeoutError:
            pass
    try:
        p.kill()
    except ProcessLookupError:
        pass
    await p.wait()


class RecorderManager:
    """Memulai/menghentikan pipeline per kamera; kamera lain tidak terganggu."""

    def __init__(self, storage: StorageConfig, live: LiveConfig):
        self.storage, self.live = storage, live
        self.recs: dict[str, CameraRecorder] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._dir_task: asyncio.Task | None = None

    def start(self, cam: Camera):
        if cam.id in self.recs:
            return
        rec = CameraRecorder(cam, self.storage, self.live)
        self.recs[cam.id] = rec
        self._tasks[cam.id] = asyncio.create_task(rec.run_forever(), name=f"rec-{cam.id}")

    async def stop(self, cam_id: str):
        self.recs.pop(cam_id, None)
        task = self._tasks.pop(cam_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def restart(self, cam: Camera):
        await self.stop(cam.id)
        self.start(cam)

    async def update(self, cam: Camera, restart: bool) -> bool:
        """Ubah kamera: mulai ulang pipeline hanya bila parameter koneksi berubah (nama saja tidak)."""
        if restart or cam.id not in self.recs:
            await self.restart(cam)
            return True
        self.recs[cam.id].cam = cam
        return False

    def start_all(self, cameras: list[Camera]):
        for cam in cameras:
            self.start(cam)
        self._dir_task = asyncio.create_task(self._dir_maker(), name="rec-dirs")

    async def stop_all(self):
        if self._dir_task:
            self._dir_task.cancel()
        for cam_id in list(self.recs):
            await self.stop(cam_id)

    async def _dir_maker(self):
        while True:    # jaga folder hari ini & besok selalu ada (ffmpeg tidak membuat folder)
            for rec in list(self.recs.values()):
                try:
                    rec._ensure_dirs()
                except OSError as err:
                    log.error("%s folder rekaman tidak bisa dibuat: %s", rec.cam.id, err)
            await asyncio.sleep(3600)
