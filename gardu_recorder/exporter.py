"""Ekspor: gabung segmen satu rentang waktu jadi satu mp4 tanpa re-encode, berjalan di latar satu per satu.

Nama segmen = jam lokal server (lihat recorder), jadi rentang ekspor juga jam lokal server.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import db
from web import filesize, tanggal

log = logging.getLogger("gardu.exporter")

MAX_RANGE = timedelta(hours=2)
MAX_JOBS = 5                     # diproses + menunggu; lebih dari itu POST /export ditolak
TIMEOUT = 30 * 60                # ffmpeg -c copy 2 jam rekaman jauh lebih cepat dari ini
_FILE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mp4$")


class ExportError(Exception):
    """Pesan untuk petugas (disimpan di riwayat dan log audit)."""


def segments(root: Path, cam: str, start: datetime, end: datetime, segment_seconds: int,
             now: datetime | None = None) -> list[Path]:
    """Segmen yang beririsan dengan [start, end), terurut. File kosong dan segmen yang mungkin masih ditulis
    ffmpeg (belum punya moov, tidak bisa digabung) dilewati: segmen dianggap selesai bila durasinya sudah lewat
    atau sudah ada segmen sesudahnya (restart di tengah menit menghasilkan segmen pendek)."""
    now = now or datetime.now()
    seg = timedelta(seconds=segment_seconds)
    found = []
    day = (start - seg).date()
    while day <= end.date() + timedelta(days=1):
        d = root / cam / day.isoformat()
        for f in sorted(d.iterdir()) if d.is_dir() else []:
            if m := _FILE_RE.match(f.name):
                found.append((datetime.combine(day, time(*map(int, m.groups()))), f))
        day += timedelta(days=1)
    out = []
    for i, (t, f) in enumerate(found):
        if not (t < end and t + seg > start and (t + seg <= now or i + 1 < len(found))):
            continue
        try:
            if f.stat().st_size:
                out.append(f)
        except OSError:
            pass
    return out


async def concat(files: list[Path], out: Path) -> None:
    lst = out.with_name(out.name + ".txt")
    lst.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in files))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", "-tag:v", "hvc1", "-movflags", "+faststart", "-f", "mp4", str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except BaseException:                       # timeout, atau aplikasi berhenti (CancelledError)
        proc.kill()
        await proc.wait()
        raise
    finally:
        lst.unlink(missing_ok=True)
    if proc.returncode != 0:
        log.error("ffmpeg concat %s gagal: %s", out.name, err.decode(errors="replace").strip()[-500:])
        raise ExportError("ffmpeg gagal menggabungkan segmen")


def range_label(start: datetime, end: datetime) -> str:
    """'18 Okt 13:40–14:00' (melewati tengah malam: '18 Okt 23:30–00:30')."""
    return f"{tanggal(start)} {start:%H:%M}–{end:%H:%M}"


@dataclass(frozen=True)
class Job:
    id: int
    cam: str
    cam_name: str
    start: datetime
    end: datetime
    pemohon: str
    ip: str


class ExportQueue:
    """Satu ekspor berjalan dalam satu waktu (beban disk NAS); sisanya menunggu dengan status 'proses'."""

    def __init__(self, root: Path, segment_seconds: int, out_dir: Path):
        self.root, self.segment_seconds, self.out_dir = root, segment_seconds, out_dir
        self._slot = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()

    def file(self, export_id: int) -> Path:
        return self.out_dir / f"{export_id}.mp4"

    def busy(self) -> int:
        return len(self._tasks)

    def submit(self, job: Job) -> None:
        task = asyncio.create_task(self._run(job), name=f"ekspor-{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job: Job) -> None:
        part = self.out_dir / f"{job.id}.part.mp4"
        try:
            async with self._slot:
                files = await asyncio.to_thread(segments, self.root, job.cam, job.start, job.end,
                                                self.segment_seconds)
                if not files:
                    raise ExportError("tidak ada rekaman di rentang itu")
                self.out_dir.mkdir(parents=True, exist_ok=True)
                await concat(files, part)
                size = part.stat().st_size
                part.replace(self.file(job.id))
        except asyncio.CancelledError:
            part.unlink(missing_ok=True)
            self._finish(job, "gagal", pesan="dihentikan: aplikasi berhenti")
            raise
        except ExportError as err:
            part.unlink(missing_ok=True)
            await asyncio.to_thread(self._finish, job, "gagal", pesan=str(err))
        except Exception:
            log.exception("ekspor %s gagal", job.id)
            part.unlink(missing_ok=True)
            await asyncio.to_thread(self._finish, job, "gagal", pesan="kesalahan di server (lihat log)")
        else:
            log.info("ekspor %s siap: %d segmen, %d byte", job.id, len(files), size)
            await asyncio.to_thread(self._finish, job, "siap", size)

    @staticmethod
    def _finish(job: Job, status: str, ukuran: int | None = None, pesan: str = "") -> None:
        db.export_finish(job.id, status, ukuran, pesan)
        hasil = f"({filesize(ukuran)})" if status == "siap" else f"— gagal: {pesan}"
        db.audit("ekspor", job.pemohon, f"Ekspor {job.cam_name} {range_label(job.start, job.end)} {hasil} · {job.ip}")
