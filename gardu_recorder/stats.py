"""Angka Dashboard: kamera online, disk, retensi + maksimum aman, bitrate."""
import math
import shutil
import time

import cleaner
import db
from config import live, storage
from recorder import RecorderManager

STARTED_AT = time.time()
VERSION = "0.1.0"


def collect(manager: RecorderManager) -> dict:
    recs = list(manager.recs.values())
    online = [r for r in recs if r.state == "online"]
    offline = [r for r in recs if r.state == "offline"]
    mbps = sum(r.mbps for r in online)
    gb_per_day = mbps * 86400 / 8 / 1000

    disk = None
    max_safe_days = None
    try:
        u = shutil.disk_usage(storage.path)
        total_gb, used_gb = u.total / 1e9, u.used / 1e9
        disk = {"percent": round(u.used / u.total * 100), "used_gb": used_gb, "total_gb": total_gb}
        # recordings_bytes None = cleaner belum selesai menghitung: tanpa itu semua isi disk dianggap bukan rekaman
        if gb_per_day > 0 and cleaner.recordings_bytes is not None:
            non_rec_gb = max(used_gb - cleaner.recordings_bytes / 1e9, 0)
            max_safe_days = max(math.floor((total_gb * storage.max_usage_percent / 100 - non_rec_gb) / gb_per_day), 0)
    except OSError:
        pass

    days = [d for d, _ in cleaner._day_dirs(storage.path)]
    return {
        "total": len(recs), "online": len(online), "offline_names": [r.cam.name for r in offline],
        "disk": disk, "disk_max": storage.max_usage_percent,
        "retention_days": storage.retention_days, "max_safe_days": max_safe_days,
        "retention_short": max_safe_days is not None and storage.retention_days > max_safe_days,
        "oldest": min(days) if days else None,
        "mbps": mbps, "gb_per_day": gb_per_day, "sizing": cleaner.recordings_bytes is None,
        "live": live, "uptime": time.time() - STARTED_AT, "version": VERSION,
        "db_bytes": db.db_size_bytes(), "now": time.time(),
    }
