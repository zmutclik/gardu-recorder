"""Konfigurasi: semua dari env/.env (rahasia, storage, live, impor awal kamera)."""
import ipaddress
import logging
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("gardu.config")

# .env satu-satunya di root repo, tidak bergantung folder tempat uvicorn dijalankan
# (di Docker tidak ada: env dari Compose)
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    GARDU_PASSWORD: str = Field(min_length=1)
    GARDU_SECRET: str = Field(min_length=32)      # hanya untuk menandatangani cookie sesi
    GARDU_DB_KEY: str                             # kunci Fernet password kamera
    GARDU_DATA_DIR: Path = Path("/data")
    DEBUG: bool = False
    # true = cookie sesi hanya lewat https; dibuka lewat http (selain localhost), login tidak bisa (lihat auth.py)
    COOKIE_SECURE: bool = True
    DISPLAY_TIMEZONE: str = "Asia/Jakarta"
    HOME_PATH: str = "/dashboard"

    # Rekaman & live (divalidasi lagi oleh StorageConfig/LiveConfig)
    GARDU_STORAGE_PATH: Path = Path("/recordings")
    GARDU_SEGMENT_SECONDS: int = 60               # tetap 60: menu Rekaman (grid per menit) menganggap segmen 1 menit
    GARDU_RETENTION_DAYS: int = 14
    GARDU_MAX_USAGE_PERCENT: int = 85
    GARDU_EXPORT_KEEP_HOURS: int = Field(24, ge=1)  # file hasil ekspor di <data>/exports; riwayat tetap ada
    GARDU_LIVE_ENABLED: bool = False
    GARDU_MEDIAMTX_URL: str = "rtsp://127.0.0.1:8554"
    GARDU_MEDIAMTX_HLS_URL: str = "http://127.0.0.1:8888"
    GARDU_MEDIAMTX_HLS_SECRET: str = ""           # = MTX_HLSCDNSECRET di MediaMTX; wajib bila live aktif
    # Jaringan tempat kamera boleh berada (CIDR). Form Kamera menolak IP lain: server tidak bisa disuruh
    # menghubungi alamat sembarang (loopback, internet) lewat tombol Uji/Simpan.
    GARDU_CAMERA_NETWORKS: list[str] = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

    # Impor awal kamera (JSON list), dibaca hanya saat tabel kamera masih kosong;
    # divalidasi per entri di import_cameras(): satu entri salah tidak menjatuhkan semua
    GARDU_CAMERAS: list[dict] = []

    @model_validator(mode="after")
    def _strong_password(self):
        if not self.DEBUG and len(self.GARDU_PASSWORD) < 12:
            raise ValueError("GARDU_PASSWORD minimal 12 karakter (kecuali DEBUG=true)")
        return self

    @field_validator("GARDU_CAMERA_NETWORKS")
    @classmethod
    def _valid_networks(cls, v: list[str]) -> list[str]:
        for net in v:
            ipaddress.ip_network(net)             # ValueError bila bukan CIDR → gagal saat start
        return v

    @field_validator("GARDU_SEGMENT_SECONDS")
    @classmethod
    def _one_minute_segments(cls, v: int) -> int:
        if v != 60:
            raise ValueError("GARDU_SEGMENT_SECONDS harus 60: menu Rekaman dan player memakai segmen 1 menit")
        return v

    @model_validator(mode="after")
    def _live_secret(self):
        if self.GARDU_LIVE_ENABLED and len(self.GARDU_MEDIAMTX_HLS_SECRET) < 16:
            raise ValueError("GARDU_MEDIAMTX_HLS_SECRET minimal 16 karakter bila GARDU_LIVE_ENABLED=true")
        return self

    @field_validator("GARDU_DB_KEY")
    @classmethod
    def _valid_fernet_key(cls, v: str) -> str:
        Fernet(v.encode())                        # ValueError bila bukan kunci Fernet → gagal saat start
        return v


class StorageConfig(BaseModel):
    path: Path = Path("/recordings")
    segment_seconds: int = Field(60, ge=10, le=3600)
    retention_days: int = Field(14, ge=1)
    max_usage_percent: int = Field(85, ge=10, le=99)


class LiveConfig(BaseModel):
    enabled: bool = False
    mediamtx_url: str = "rtsp://127.0.0.1:8554"      # recorder publish ke sini
    hls_url: str = "http://127.0.0.1:8888"           # Gardu meneruskan HLS dari sini (proxy /live)
    hls_secret: str = ""                             # Bearer ke MediaMTX: Gardu dianggap "CDN" → satu sesi per kamera
    publish_user: str = "gardu"                      # hanya user ini yang boleh publish (lihat deploy/mediamtx.yml);
    publish_password: str = ""                       # passwordnya = hls_secret (rahasia bersama Gardu ↔ MediaMTX)


class ImportCamera(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    name: str
    ip: str
    port: int = 8800
    device_id: int
    username: str
    password: str
    quality: int = Field(1, ge=0, le=1)
    fps: int = Field(16, ge=1, le=60)


def import_cameras(raw_list: list[dict]) -> list[ImportCamera]:
    out = []
    for raw in raw_list:
        try:
            out.append(ImportCamera.model_validate(raw))
        except Exception as err:
            log.error("entri kamera di GARDU_CAMERAS dilewati: %s", err)
    return out


settings = Settings()
storage = StorageConfig(path=settings.GARDU_STORAGE_PATH, segment_seconds=settings.GARDU_SEGMENT_SECONDS,
                        retention_days=settings.GARDU_RETENTION_DAYS,
                        max_usage_percent=settings.GARDU_MAX_USAGE_PERCENT)
live = LiveConfig(enabled=settings.GARDU_LIVE_ENABLED, mediamtx_url=settings.GARDU_MEDIAMTX_URL,
                  hls_url=settings.GARDU_MEDIAMTX_HLS_URL, hls_secret=settings.GARDU_MEDIAMTX_HLS_SECRET,
                  publish_password=settings.GARDU_MEDIAMTX_HLS_SECRET)
camera_networks = [ipaddress.ip_network(n) for n in settings.GARDU_CAMERA_NETWORKS]


def camera_ip_allowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in camera_networks)
