"""Retensi rekaman: hapus hari lewat retensi, lalu hari tertua sampai pemakaian disk di bawah batas.
Juga file hasil ekspor yang lebih tua dari GARDU_EXPORT_KEEP_HOURS (tidak ikut retensi rekaman)."""
import asyncio
import logging
import os
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

from config import StorageConfig

log = logging.getLogger("gardu.cleaner")

# Diisi tiap putaran cleaner; dipakai Dashboard (hitung "retensi maksimum aman") tanpa menelusuri NAS per request
recordings_bytes: int | None = None


def _day_dirs(root: Path) -> list[tuple[date, Path]]:
    out = []
    if not root.exists():
        return out
    for cam in root.iterdir():
        if not cam.is_dir():
            continue
        for d in cam.iterdir():
            try:
                out.append((date.fromisoformat(d.name), d))
            except ValueError:
                pass
    return sorted(out)


def _usage(root: Path) -> float:
    u = shutil.disk_usage(root)
    return u.used / u.total * 100


def _tree_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return total


def clean_once(root: Path, retention_days: int, max_pct: int):
    global recordings_bytes
    if not root.exists():
        return
    cutoff = date.today() - timedelta(days=retention_days - 1)    # retensi N hari = hari ini + N-1 hari sebelumnya
    for day, d in _day_dirs(root):
        if day < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            log.info("hapus (retensi) %s", d)
    dirs = [x for x in _day_dirs(root) if x[0] < date.today()]   # jangan hapus hari ini
    while _usage(root) > max_pct and dirs:
        _day, d = dirs.pop(0)
        shutil.rmtree(d, ignore_errors=True)
        log.warning("hapus (disk penuh) %s", d)
    recordings_bytes = _tree_size(root)


def clean_exports(exports_dir: Path, keep_hours: int) -> None:
    cutoff = time.time() - keep_hours * 3600
    for f in exports_dir.glob("*.mp4") if exports_dir.is_dir() else []:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info("hapus (ekspor lama) %s", f.name)
        except OSError:
            pass


async def run_cleaner(storage: StorageConfig, exports_dir: Path, export_keep_hours: int):
    root = Path(storage.path)
    while True:
        try:
            await asyncio.to_thread(clean_once, root, storage.retention_days, storage.max_usage_percent)
        except Exception:
            log.exception("cleaner gagal")
        try:
            await asyncio.to_thread(clean_exports, exports_dir, export_keep_hours)
        except Exception:
            log.exception("pembersihan ekspor gagal")
        await asyncio.sleep(3600)
