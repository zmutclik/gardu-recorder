"""SQLite (satu file di volume): kamera (password terenkripsi), exports, audit_log. Akses sinkron lewat sqlite3;
handler `def` berjalan di threadpool FastAPI, kerja DB singkat."""
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field

from config import ImportCamera, settings
from cryptography.fernet import Fernet, InvalidToken

DB_PATH = settings.GARDU_DATA_DIR / "gardu.db"
_fernet = Fernet(settings.GARDU_DB_KEY.encode())

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    ip           TEXT NOT NULL UNIQUE,
    port         INTEGER NOT NULL DEFAULT 8800,
    device_id    INTEGER NOT NULL,
    username     TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    quality      INTEGER NOT NULL DEFAULT 1,
    fps          INTEGER NOT NULL DEFAULT 16,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu      TEXT NOT NULL DEFAULT (datetime('now')),   -- UTC
    jenis      TEXT NOT NULL,
    pelaku     TEXT NOT NULL,
    keterangan TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_jenis_id ON audit_log (jenis, id DESC);
CREATE TABLE IF NOT EXISTS exports (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu    TEXT NOT NULL DEFAULT (datetime('now')),     -- UTC, saat diminta
    kamera   TEXT NOT NULL,                               -- id kamera (folder rekaman)
    nama     TEXT NOT NULL,                               -- nama kamera saat diminta
    mulai    TEXT NOT NULL,                               -- jam lokal server 'YYYY-MM-DD HH:MM' (= nama file segmen)
    selesai  TEXT NOT NULL,
    pemohon  TEXT NOT NULL,
    ip       TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'proses',              -- proses | siap | gagal
    ukuran   INTEGER,
    pesan    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_exports_waktu ON exports (waktu);
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,                -- acak, juga disimpan di cookie sesi
    exp REAL NOT NULL                    -- epoch detik
);
"""

LOG_KINDS = ("ekspor", "login", "kamera")


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    ip: str
    port: int
    device_id: int
    username: str
    quality: int
    fps: int
    password: str = field(repr=False)   # hanya untuk recorder/uji; tidak pernah dikirim ke browser


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    # Hanya pemilik proses: log audit + password kamera terenkripsi. -wal/-shm dibuat SQLite dengan izin yang sama.
    for f in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"), DB_PATH.with_name(DB_PATH.name + "-shm")):
        if f.exists():
            f.chmod(0o600)


def check_key() -> None:
    """Saat start: GARDU_DB_KEY harus bisa membuka password kamera tersimpan (kunci hilang/diganti → pesan jelas)."""
    with connect() as conn:
        rows = conn.execute("SELECT id, password_enc FROM cameras").fetchall()
    bad = []
    for r in rows:
        try:
            _fernet.decrypt(r["password_enc"].encode())
        except InvalidToken:
            bad.append(r["id"])
    if bad:
        raise RuntimeError(f"GARDU_DB_KEY tidak cocok dengan password kamera di {DB_PATH} ({', '.join(bad)}). "
                           "Pakai kunci yang dulu dipakai (dari cadangan .env). Bila kunci itu hilang: hapus kamera "
                           "tersebut (sqlite3 gardu.db \"DELETE FROM cameras WHERE id = '<id>'\") lalu tambahkan "
                           "ulang lewat menu Kamera; rekaman lama tetap ada.")


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def _camera(row: sqlite3.Row) -> Camera:
    d = dict(row)
    d["password"] = _fernet.decrypt(d.pop("password_enc").encode()).decode()
    d.pop("created_at")
    return Camera(**d)


def list_cameras() -> list[Camera]:
    with connect() as conn:
        return [_camera(r) for r in conn.execute("SELECT * FROM cameras ORDER BY created_at, id")]


def get_camera(cam_id: str) -> Camera | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,)).fetchone()
    return _camera(row) if row else None


def count_cameras() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]


def insert_camera(cam: ImportCamera) -> None:
    """Melempar sqlite3.IntegrityError bila id atau IP sudah dipakai."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO cameras (id, name, ip, port, device_id, username, password_enc, quality, fps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cam.id, cam.name, cam.ip, cam.port, cam.device_id, cam.username, encrypt(cam.password),
             cam.quality, cam.fps),
        )


def update_camera(cam_id: str, cam: ImportCamera) -> None:
    """Password kosong = tidak diubah. Melempar IntegrityError bila IP bentrok."""
    with connect() as conn:
        conn.execute(
            "UPDATE cameras SET name=?, ip=?, port=?, device_id=?, username=?, quality=?, fps=? WHERE id=?",
            (cam.name, cam.ip, cam.port, cam.device_id, cam.username, cam.quality, cam.fps, cam_id),
        )
        if cam.password:
            conn.execute("UPDATE cameras SET password_enc=? WHERE id=?", (encrypt(cam.password), cam_id))


def delete_camera(cam_id: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM cameras WHERE id = ?", (cam_id,)).rowcount > 0


def audit(jenis: str, pelaku: str, keterangan: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit_log (jenis, pelaku, keterangan) VALUES (?, ?, ?)",
                     (jenis, pelaku, keterangan))


def recent_logs(jenis: str, limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT waktu, pelaku, keterangan FROM audit_log WHERE jenis = ? ORDER BY id DESC LIMIT ?",
            (jenis, limit),
        ).fetchall()


def export_create(kamera: str, nama: str, mulai: str, selesai: str, pemohon: str, ip: str) -> int:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO exports (kamera, nama, mulai, selesai, pemohon, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (kamera, nama, mulai, selesai, pemohon, ip)).lastrowid


def export_finish(export_id: int, status: str, ukuran: int | None = None, pesan: str = "") -> None:
    with connect() as conn:
        conn.execute("UPDATE exports SET status = ?, ukuran = ?, pesan = ? WHERE id = ?",
                     (status, ukuran, pesan, export_id))


def export_get(export_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()


def exports_since(waktu_utc: str) -> list[sqlite3.Row]:
    """Riwayat sejak waktu itu (UTC), ditambah yang masih diproses, terbaru dulu."""
    with connect() as conn:
        return conn.execute("SELECT * FROM exports WHERE waktu >= ? OR status = 'proses' ORDER BY id DESC",
                            (waktu_utc,)).fetchall()


def exports_count_running() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM exports WHERE status = 'proses'").fetchone()[0]


def exports_abort_running(pesan: str) -> None:
    """Saat start: ekspor yang tercatat 'proses' berasal dari proses lama yang sudah berhenti."""
    with connect() as conn:
        conn.execute("UPDATE exports SET status = 'gagal', pesan = ? WHERE status = 'proses'", (pesan,))


def session_create(sid: str, exp: float, now: float) -> None:
    """Sesi login di server: logout benar-benar mencabut sesi (cookie saja tidak bisa). Sekalian buang yang lama."""
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE exp < ?", (now,))
        conn.execute("INSERT INTO sessions (sid, exp) VALUES (?, ?)", (sid, exp))


def session_valid(sid: str, now: float) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM sessions WHERE sid = ? AND exp > ?", (sid, now)).fetchone() is not None


def session_delete(sid: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))


def db_size_bytes() -> int:
    try:
        return DB_PATH.stat().st_size
    except FileNotFoundError:
        return 0
