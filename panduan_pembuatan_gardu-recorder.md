# Panduan Pembuatan Gardu (NVR V380)

Oct 18, 2026 · @Fahrudin Hariadi

## Ringkasan

Gardu adalah satu aplikasi NVR self-hosted untuk kamera CCTV V380: mengambil video langsung dari kamera lewat protokol asli V380 (TCP port 8800), tanpa RTSP di sisi kamera, menyimpannya per segmen 1 menit ke NAS, dan menyajikan web untuk live view serta playback dengan satu password.

- **Metode ambil video**: mengikuti [zmutclik/v380\_stream](https://github.com/zmutclik/v380_stream) (lisensi MIT): login AES ke port 8800, minta stream, dekripsi, keluarannya H.265 mentah (Annex-B) di stdout.
- **gardu-recorder**: menjalankan client V380 per kamera, mem-pipe data ke ffmpeg untuk disimpan sebagai segmen mp4, watchdog, retensi, serta web UI dan API.
- **gardu-live**: MediaMTX untuk live view, diisi salinan stream dari gardu-recorder.
- **Akses**: tanpa sistem user; satu password untuk masuk web.
- **Stack**: Python 3.12, FastAPI, SQLite (data kamera), ffmpeg, `cryptography`, MediaMTX, Docker Compose.
- **Syarat kamera**: firmware V380 v31/v32 (format paket terenkripsi yang didukung metode ini).
- **Di luar ruang lingkup tahap awal**: banyak gardu/pusat kendali, akun per user, deteksi objek AI, notifikasi, aplikasi mobile.

## Arsitektur

&#91;embedded content: arsitektur gardu-recorder dan Markas\]

Gardu-recorder login langsung ke kamera di port 8800, mendekripsi stream H.265, lalu menyimpannya ke NAS; salinan stream yang sama diteruskan ke MediaMTX untuk live view, jadi kamera cukup melayani satu koneksi. Petugas membuka web Gardu dengan satu password untuk melihat status, live view, rekaman, dan ekspor.

## Persiapan

Langkah pertama adalah memastikan setiap kamera bisa login dan mengirim video lewat port 8800 memakai skrip `v380_stream.py`.

1. Beri IP statis (DHCP reservation) untuk setiap kamera.
2. Catat untuk setiap kamera: IP, **Device ID** (terlihat di aplikasi V380 Pro), username, password, dan versi firmware.
3. Matikan pembaruan firmware otomatis bila ada. Metode ini hasil reverse-engineering; firmware baru bisa mengubah protokol.
4. Uji login dan dekripsi dengan mengambil satu snapshot:

```bash
pip install cryptography
python v380_stream.py --ip 192.168.10.51 --id 12345678 \
  --user admin --password RAHASIA --snapshot uji.jpg
```

5. Bila `uji.jpg` terbentuk dan gambarnya benar, uji stream 1 menit ke file lalu putar dengan VLC/mpv:

```bash
timeout 60 python v380_stream.py --ip 192.168.10.51 --id 12345678 \
  --user admin --password RAHASIA --output uji.h265
ffprobe -f hevc uji.h265   # catat resolusi & fps
```

6. Kamera yang gagal: cek port 8800 terbuka (`nc -vz IP 8800`), cocokkan Device ID dan akun, cek versi firmware.

**Kebutuhan server**

| Komponen | Kebutuhan |
| --- | --- |
| Server gardu | Linux + Docker; dekripsi AES ringan, ±1 proses Python + 1 ffmpeg per kamera; 2 vCPU, 2 GB RAM cukup untuk ±10 kamera |
| Storage | NAS via NFS/SMB, atau volume Synology langsung |
| Tools | Python 3.12 + `cryptography`, ffmpeg, MediaMTX (live view) |
| Jaringan | Kamera, gardu, NAS satu VLAN; port TCP 8800 kamera terjangkau dari gardu |

Perkiraan kapasitas: ukur dari file `uji.h265` (ukuran ÷ 60 detik), lalu kalikan 86.400 untuk per hari. Kamera resolusi tinggi seperti 2304×2592 bisa beberapa GB per hari per kamera.

## Struktur proyek

Satu repo `gardu` berisi satu aplikasi, `gardu_recorder`, yang merekam sekaligus menyajikan web; client V380 dari repo referensi disalin ke folder `vendor/` beserta lisensinya.

```
gardu/
├── gardu_recorder/
│   ├── main.py            # FastAPI + startup recorder & cleaner
│   ├── config.py          # baca .env (rahasia, storage, live, impor awal kamera)
│   ├── db.py              # SQLite: tabel cameras (password terenkripsi), exports, audit_log
│   ├── auth.py            # login satu password
│   ├── recorder.py        # client V380 → ffmpeg, watchdog
│   ├── cleaner.py         # retensi rekaman
│   ├── exporter.py        # gabung segmen jadi satu mp4
│   ├── vendor/
│   │   ├── v380_stream.py # dari zmutclik/v380_stream
│   │   └── LICENSE        # MIT, wajib ikut disertakan
│   ├── api/
│   │   ├── cameras.py     # status, snapshot, tambah/ubah/hapus, uji kamera
│   │   ├── recordings.py  # daftar & stream file, timeline
│   │   ├── export.py      # ekspor rentang waktu (antrean + riwayat + unduh)
│   │   └── log.py         # GET /log/{jenis} dari audit_log
│   └── web/               # halaman HTML (Jinja2) + JS player
├── deploy/
│   └── mediamtx.yml
├── Dockerfile             # context build = root, hanya gardu_recorder/ yang disalin (.dockerignore)
├── docker-compose.yml     # docker compose up -d dari root
├── .env                   # satu-satunya .env (dev dan Docker), tidak masuk git
└── .env.example           # contoh semua konfigurasi
```

**Konfigurasi (`.env`) dan data kamera (SQLite)**

Kamera dikelola dari web (menu Kamera: tambah, ubah, hapus) dan disimpan di SQLite (`/data/gardu.db`, satu file di volume Docker). Semua konfigurasi lain ada di `.env` (contoh lengkap: `.env.example`): rahasia, lokasi data, pengaturan rekaman dan live yang jarang berubah. Bila `GARDU_CAMERAS` terisi dan tabel kamera masih kosong, daftar itu diimpor ke database satu kali saat gardu pertama berjalan; sesudahnya database adalah sumber data dan `GARDU_CAMERAS` diabaikan.

Skema tabel `cameras`: `id` (slug, unik), `name`, `ip` (unik), `port` (8800), `device_id`, `username`, `password_enc`, `quality`, `fps`. Password kamera disimpan terenkripsi (Fernet dari `cryptography`) dengan kunci `GARDU_DB_KEY` di `.env`; password tidak pernah dikirim kembali ke browser, dan saat ubah kamera, kolom password yang dikosongkan berarti tidak diubah. Setiap tambah/ubah/hapus langsung memulai, memulai ulang, atau menghentikan pipeline kamera yang bersangkutan saja; kamera lain tidak terganggu. Menghapus kamera tidak menghapus rekaman yang sudah ada di NAS; folder itu dibersihkan oleh cleaner sesuai retensi.

Contoh `.env` (selain rahasia, lihat bagian Login satu password), juga format impor awal:

```bash
GARDU_STORAGE_PATH=/recordings
GARDU_SEGMENT_SECONDS=60                   # tetap 60: menu Rekaman memakai grid per menit
GARDU_RETENTION_DAYS=14
GARDU_MAX_USAGE_PERCENT=85

GARDU_LIVE_ENABLED=true
GARDU_MEDIAMTX_URL=rtsp://127.0.0.1:8554   # internal, hanya gardu → MediaMTX

# JSON satu baris; quality 1 = HD, 0 = SD; fps dari hasil ffprobe saat persiapan
GARDU_CAMERAS='[{"id":"igd-pintu","name":"IGD Pintu Masuk","ip":"192.168.10.51","port":8800,"device_id":12345678,"username":"admin","password":"...","quality":1,"fps":16},{"id":"igd-lobby","name":"IGD Lobby","ip":"192.168.10.52","port":8800,"device_id":23456789,"username":"admin","password":"...","quality":1,"fps":16}]'
```

`.env` tidak masuk git. `config.py` membacanya dengan `pydantic-settings` dan memvalidasi tiap nilai (mis. retensi minimal 1 hari); entri `GARDU_CAMERAS` divalidasi satu per satu, sehingga satu entri salah hanya dilewati. Password kamera yang diimpor langsung disimpan terenkripsi di database, jadi `GARDU_CAMERAS` boleh dihapus dari `.env` sesudah impor pertama.

## Recorder dan watchdog

Setiap kamera punya satu pipeline: client V380 menulis H.265 mentah ke stdout, Python memompanya ke ffmpeg yang menyimpan segmen mp4 tanpa re-encode, dan bila data berhenti mengalir lebih dari 15 detik seluruh pipeline dinyalakan ulang.

```
v380_stream.py (stdout: H.265 Annex-B)
      │  dipompa oleh recorder.py (hitung byte, deteksi macet)
      ├──▶ ffmpeg #1 → /recordings/<kamera>/<tgl>/<jam>.mp4   (wajib)
      └──▶ ffmpeg #2 → MediaMTX (live view)                    (opsional)
```

Satu koneksi ke kamera dipakai untuk rekam dan live sekaligus, karena kamera V380 murah biasanya membatasi jumlah koneksi.

**Perintah ffmpeg perekam**

```bash
ffmpeg -hide_banner -loglevel warning \
  -f hevc -framerate 16 -fflags +genpts -i pipe:0 \
  -map 0:v -c:v copy -tag:v hvc1 \
  -f segment -segment_time 60 -segment_atclocktime 1 \
  -reset_timestamps 1 -strftime 1 \
  -segment_format mp4 \
  -segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof \
  "/recordings/igd-pintu/%Y-%m-%d/%H-%M-%S.mp4"
```

- `-f hevc -framerate 16`: input berupa H.265 mentah tanpa timestamp, jadi fps harus disebut. Isi sesuai hasil `ffprobe` saat persiapan; fps salah membuat durasi video lebih cepat atau lambat.
- `movflags=+frag_keyframe+empty_moov`: mp4 terfragmentasi, tetap bisa diputar walau ffmpeg mati mendadak dan bisa diputar saat segmen masih ditulis.
- `-tag:v hvc1`: agar H.265 di mp4 dikenali Safari/QuickTime.
- Folder tanggal harus dibuat lebih dulu; ffmpeg tidak membuat folder.

**`recorder.py` (inti)**

```python
import asyncio, logging, os, sys, time
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("gardu.recorder")
V380 = Path(__file__).parent / "vendor" / "v380_stream.py"
STALL_SECONDS = 15

class CameraRecorder:
    def __init__(self, cam, storage, live):
        self.cam, self.live = cam, live
        self.root = Path(storage.path) / cam.id
        self.segment = storage.segment_seconds
        self.online = False
        self.restarts = 0
        self.bytes_total = 0
        self.last_data = 0.0

    def _ensure_dirs(self):
        for d in (date.today(), date.today() + timedelta(days=1)):
            (self.root / d.isoformat()).mkdir(parents=True, exist_ok=True)

    def _v380_env(self):
        c = self.cam
        return {**os.environ,
                "V380_IP": c.ip, "V380_PORT": str(c.port),
                "V380_DEVICE_ID": str(c.device_id),
                "V380_USERNAME": c.username, "V380_PASSWORD": c.password,
                "V380_QUALITY": str(c.quality), "V380_OUTPUT": "-",
                "RTSP_MODE": "production"}

    def _rec_cmd(self):
        out = str(self.root / "%Y-%m-%d" / "%H-%M-%S.mp4")
        return ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "hevc", "-framerate", str(self.cam.fps),
                "-fflags", "+genpts", "-i", "pipe:0",
                "-map", "0:v", "-c:v", "copy", "-bsf:v", "setts=pts=DTS", "-tag:v", "hvc1",
                "-f", "segment", "-segment_time", str(self.segment),
                "-segment_atclocktime", "1", "-reset_timestamps", "1",
                "-strftime", "1", "-segment_format", "mp4",
                "-segment_format_options",
                "movflags=+frag_keyframe+empty_moov+default_base_moof", out]

    def _live_cmd(self):
        return ["ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "hevc", "-framerate", str(self.cam.fps),
                "-fflags", "+genpts", "-i", "pipe:0", "-c:v", "copy",
                "-bsf:v", "setts=pts=DTS",   # tanpa ini PTS di RTSP = 0 dan MediaMTX tak pernah membuat segmen HLS
                "-f", "rtsp", "-rtsp_transport", "tcp", f"{self.live.mediamtx_url}/{self.cam.id}"]

    async def run_forever(self):
        backoff = 2
        while True:
            self._ensure_dirs()
            started = time.time()
            await self._run_once()
            self.online = False
            self.restarts += 1
            backoff = 2 if time.time() - started > 60 else min(backoff * 2, 60)
            log.warning("%s pipeline berhenti, ulang dalam %ss", self.cam.id, backoff)
            await asyncio.sleep(backoff)

    async def _run_once(self):
        PIPE = asyncio.subprocess.PIPE
        src = await asyncio.create_subprocess_exec(
            sys.executable, str(V380), stdout=PIPE, env=self._v380_env())
        rec = await asyncio.create_subprocess_exec(*self._rec_cmd(), stdin=PIPE)
        live = None
        if self.live.enabled:
            live = await asyncio.create_subprocess_exec(*self._live_cmd(), stdin=PIPE)
        try:
            while True:
                chunk = await asyncio.wait_for(src.stdout.read(65536), STALL_SECONDS)
                if not chunk:
                    break                      # client V380 keluar
                self.online = True
                self.bytes_total += len(chunk)
                self.last_data = time.time()
                rec.stdin.write(chunk)
                await rec.stdin.drain()        # rekaman tidak boleh hilang
                if live and live.returncode is None:
                    try:
                        if live.stdin.transport.get_write_buffer_size() > 8_000_000:
                            live.kill()            # live tertinggal
                            live = None
                            continue
                        live.stdin.write(chunk)  # live best-effort
                    except (BrokenPipeError, ConnectionResetError):
                        live = None
        except asyncio.TimeoutError:
            log.warning("%s tidak ada data %ss (macet)", self.cam.id, STALL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            log.warning("%s ffmpeg perekam mati", self.cam.id)
        finally:
            await _stop(src)
            await _stop(rec, graceful=True)   # tutup stdin → segmen ditutup rapi
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
        except asyncio.TimeoutError:
            pass
    p.kill()
    await p.wait()

async def start_all(cameras, storage, live):
    recs = {c.id: CameraRecorder(c, storage, live) for c in cameras}
    for r in recs.values():
        asyncio.create_task(r.run_forever())
    asyncio.create_task(_dir_maker(recs))
    return recs

async def _dir_maker(recs):
    while True:  # jaga folder hari ini & besok selalu ada
        for r in recs.values():
            r._ensure_dirs()
        await asyncio.sleep(3600)
```

**Catatan penting**

- Live view memakai `write()` tanpa `drain()` supaya MediaMTX yang lambat tidak menahan rekaman. Bila buffer live menumpuk, proses live dimatikan dan rekaman tetap jalan.
- `bytes_total` dan `last_data` ditampilkan di Dashboard sebagai indikator kesehatan, misalnya bitrate per menit.
- Pastikan `v380_stream.py` hanya menulis video ke stdout dan log ke stderr; tulisan lain di stdout akan merusak stream H.265.
- Desain ini tidak bergantung pada kemampuan reconnect di client V380, karena setiap putus koneksi seluruh pipeline dibuat ulang.
- **Segmen 1 menit** menghasilkan 1.440 file per kamera per hari. Itu aman untuk ext4/btrfs; API playback sebaiknya mengelompokkan daftar per jam agar UI tidak berat. ffmpeg hanya bisa memotong di keyframe, jadi bila interval keyframe kamera panjang (misalnya 4 detik), panjang segmen bisa meleset beberapa detik.

## Retensi rekaman

Cleaner berjalan tiap jam dengan dua aturan: simpan tepat `retention_days` hari termasuk hari ini (14 = hari ini + 13 hari sebelumnya) dan hapus folder tanggal yang lebih tua, lalu bila disk masih di atas `max_usage_percent`, hapus hari tertua satu per satu sampai aman.

```python
import asyncio, shutil, logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("gardu.cleaner")

def _day_dirs(root: Path):
    out = []
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

def clean_once(root: Path, retention_days: int, max_pct: int):
    cutoff = date.today() - timedelta(days=retention_days - 1)    # hari ini + N-1 hari sebelumnya
    dirs = _day_dirs(root)
    for day, d in dirs:
        if day < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            log.info("hapus (retensi) %s", d)
    dirs = [x for x in _day_dirs(root) if x[0] < date.today()]  # jangan hapus hari ini
    while _usage(root) > max_pct and dirs:
        day, d = dirs.pop(0)
        shutil.rmtree(d, ignore_errors=True)
        log.warning("hapus (disk penuh) %s", d)

async def run_cleaner(storage):
    root = Path(storage.path)
    while True:
        await asyncio.to_thread(clean_once, root,
                                storage.retention_days, storage.max_usage_percent)
        await asyncio.sleep(3600)
```

Bila NAS dipakai bersama aplikasi lain, `disk_usage` mengukur seluruh volume. Pertimbangkan shared folder khusus dengan kuota di Synology agar batasnya jelas.

**Retensi maksimum aman (untuk Dashboard).** Dashboard menampilkan berapa hari retensi yang masih muat di bawah batas disk, dihitung dari bitrate kamera yang sedang online:

```
GB_per_hari   = jumlah_bitrate_Mbps × 86.400 ÷ 8 ÷ 1.000        # mis. 14,5 Mbps ≈ 157 GB/hari
maks_aman_hari = floor((total_GB × max_usage_percent ÷ 100 − pemakaian_non_rekaman_GB) ÷ GB_per_hari)
```

Bila `retention_days` lebih besar dari angka itu, cleaner akan menghapus hari tertua lebih awal (aturan kedua di atas), jadi retensi efektif lebih pendek dari yang diset; KPI Retensi memberi tanda dan menampilkan angka maksimumnya.

## API playback dan live view

FastAPI di gardu-recorder menyajikan status kamera dan file rekaman; live view disajikan MediaMTX dari salinan stream yang dikirim recorder, bukan dari koneksi baru ke kamera.

**Endpoint**

| Method | Path | Fungsi |
| --- | --- | --- |
| GET | `/health` | status gardu, uptime, pemakaian disk |
| GET | `/cameras` | daftar kamera, online/offline, restart, bitrate |
| GET | `/recordings/{cam}/{tanggal}` | daftar segmen per hari |
| GET | `/recordings/{cam}/{tanggal}/{file}` | putar/unduh segmen (mendukung Range) |
| GET | `/live/{cam}` | proxy HLS dari MediaMTX (butuh login) |
| POST | `/export` | buat ekspor (latar), mengembalikan id |
| GET | `/export` | riwayat ekspor hari ini |
| GET | `/export/{id}/download` | unduh hasil ekspor yang berstatus siap |
| GET | `/log/{jenis}` | log audit: `ekspor`, `login`, atau `kamera` |

Endpoint kamera (`/cameras…`) dan timeline rekaman ada di tabel menu pada bagian Menu dan web UI.

**`api/recordings.py`**

```python
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/recordings")
ROOT = Path("/recordings").resolve()

def _safe(*parts) -> Path:
    p = ROOT.joinpath(*parts).resolve()
    if ROOT not in p.parents:
        raise HTTPException(400, "path tidak valid")
    if not p.exists():
        raise HTTPException(404)
    return p

@router.get("/{cam}/{day}")
def list_day(cam: str, day: str):
    d = _safe(cam, day)
    return [{"file": f.name, "size": f.stat().st_size}
            for f in sorted(d.glob("*.mp4"))]

@router.get("/{cam}/{day}/{name}")
def get_file(cam: str, day: str, name: str):
    return FileResponse(_safe(cam, day, name), media_type="video/mp4")
```

`FileResponse` di Starlette versi terbaru sudah mendukung header Range, sehingga video bisa di-seek di browser.

**Catatan H.265 di browser**: dukungan pemutaran H.265 berbeda antar browser dan perangkat. Safari umumnya bisa; Chrome/Edge tergantung dukungan dekoder hardware; Firefox belum tentu. Uji di PC petugas lebih dulu. Bila tidak bisa, sediakan endpoint unduh untuk diputar di VLC, atau transcode ke H.264 saat diminta (berat di CPU, jadi hanya untuk potongan pendek).

**MediaMTX (`deploy/mediamtx.yml`)**

File lengkap ada di `deploy/mediamtx.yml` (diuji dengan MediaMTX v1.21.1). Intinya:

```yaml
rtspAddress: 127.0.0.1:8554       # hanya untuk menerima publish dari gardu-recorder
hlsAddress: 127.0.0.1:8888
hlsVariant: fmp4                  # H.265 di HLS wajib fMP4
hlsAlwaysRemux: true              # playlist siap sebelum ada penonton
webrtc: false                     # protokol lain (rtmp, srt, moq, webrtc) dimatikan
paths:
  all_others:
    source: publisher    # path dibuat saat recorder publish /<id-kamera>
```

Semua port MediaMTX hanya listen di `127.0.0.1`, sehingga tidak bisa dibuka langsung dari jaringan. Petugas membuka menu Live View; player memuat `/live/<id-kamera>/index.m3u8` yang diteruskan Gardu ke HLS MediaMTX setelah login (lihat bagian Login satu password).

**`hlsCDNSecret` wajib**: sejak v1.21, MediaMTX membuat sesi per penonton lewat redirect `?cookieCheck=1`, yang tidak cocok dengan proxy. Gardu mengirim `Authorization: Bearer <GARDU_MEDIAMTX_HLS_SECRET>` sehingga dianggap CDN: satu sesi per kamera untuk semua penonton. Compose meneruskan nilai yang sama ke MediaMTX sebagai `MTX_HLSCDNSECRET`.

**Player**: `hls.js` (disimpan lokal di `web/static/js/vendor/`, worker dimatikan karena CSP) untuk Chrome/Edge/Firefox, HLS bawaan untuk Safari. Tile yang gagal mencoba ulang tiap 5 detik; di tampilan 1 kamera, deretan kamera lain memakai snapshot (bukan stream) agar browser tidak mendekode banyak H.265 sekaligus.

## Login satu password

Web Gardu dilindungi satu password bersama tanpa daftar user: password disimpan sebagai hash bcrypt di `.env`, login memberi cookie sesi bertanda tangan berlaku 12 jam, dan 5 kali salah dari satu IP dikunci 5 menit.

**Isi `.env`**

```bash
# buat hash: python -c "import bcrypt;print(bcrypt.hashpw(b'PasswordGardu', bcrypt.gensalt()).decode())"
GARDU_PASSWORD_HASH='$2b$12$....'      # tanda kutip tunggal agar $ tidak diartikan variabel
GARDU_SECRET='string-acak-panjang'     # untuk menandatangani cookie sesi
GARDU_DB_KEY='...'                     # kunci Fernet password kamera di database
```

**`auth.py`**

```python
import os, time, bcrypt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter()
PASSWORD_HASH = os.environ["GARDU_PASSWORD_HASH"].encode()
MAX_FAIL, LOCK_SECONDS, SESSION_SECONDS = 5, 300, 12 * 3600
_fails: dict[str, list[float]] = {}

@router.post("/login")
def login(request: Request, password: str = Form(...)):
    ip, now = request.client.host, time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAIL:
        raise HTTPException(429, "Terlalu banyak percobaan, tunggu 5 menit")
    if not bcrypt.checkpw(password.encode(), PASSWORD_HASH):
        _fails[ip] = recent + [now]
        return RedirectResponse("/login?salah=1", status_code=303)
    _fails.pop(ip, None)
    request.session.update(ok=True, exp=now + SESSION_SECONDS)
    return RedirectResponse("/", status_code=303)

@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

def require_login(request: Request):
    s = request.session
    if not s.get("ok") or s.get("exp", 0) < time.time():
        raise HTTPException(401)
```

**Di `main.py`**

```python
import os
from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware
from auth import router as auth_router, require_login
from api import cameras, recordings, export

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ["GARDU_SECRET"],
                   max_age=12 * 3600, same_site="strict")
app.include_router(auth_router)
for r in (cameras.router, recordings.router, export.router):
    app.include_router(r, dependencies=[Depends(require_login)])
```

Halaman web yang kena 401 diarahkan ke `/login` lewat exception handler. Tambahkan `bcrypt`, `itsdangerous`, `python-multipart`, dan `jinja2` ke `requirements.txt`.

**Live view juga harus terlindungi.** MediaMTX punya port sendiri (8889/8888), sehingga password Gardu tidak otomatis berlaku di sana. Pilih salah satu:

- **HLS lewat Gardu (paling sederhana)**: MediaMTX hanya listen di `127.0.0.1`, lalu FastAPI meneruskan `/live/{cam}/...` ke `http://127.0.0.1:8888/{cam}/...` dengan `require_login`. Jeda sekitar 2–6 detik.
- **WebRTC (jeda < 1 detik)**: aktifkan autentikasi HTTP eksternal di MediaMTX, lalu Gardu memberi token sementara di URL live dan memvalidasinya lewat endpoint internal. Cek format permintaan auth di dokumentasi MediaMTX versi yang dipakai.

Konsekuensi satu password: tidak ada akun yang bisa dicatat. Untuk privasi pasien, nama pemohon wajib diisi saat ekspor dan disimpan di log audit; login (berhasil/gagal) dan perubahan kamera dicatat dengan alamat IP (lihat Log audit di bagian Menu dan web UI).

## Menu dan web UI

Web Gardu punya lima menu setelah login; semua halaman dirender FastAPI + Jinja2 dengan sedikit JavaScript untuk player dan timeline.

| Menu | Isi | Endpoint |
| --- | --- | --- |
| Dashboard | Empat KPI: kamera online (dengan nama yang offline), pemakaian disk NAS, retensi + maksimum aman (±N hari), bitrate total (≈ GB/hari). Tiga kartu ringkasan yang hanya tampil: live view, keamanan & ekspor, aplikasi (versi, lama aktif, ukuran SQLite). Log dengan tiga tab: ekspor, login, perubahan kamera. Segarkan otomatis tiap 10 detik (bisa dimatikan) | `GET /health`, `GET /cameras`, `GET /log/{jenis}` |
| Live View | Grid 1/4/9 kamera. Klik satu kamera (atau tombol Layar penuh) untuk tampil 1 kamera; di bawahnya deretan thumbnail kamera lain untuk berpindah tanpa kembali ke grid | `/live/{cam}/` (proxy HLS) |
| Rekaman | Pilih kamera + tanggal, lalu pilih jam (24 tombol, masing-masing menampilkan jumlah menit yang terekam), lalu menit (60 kotak; menit tanpa rekaman diberi warna lain). Player menyambung segmen otomatis, kecepatan 1×/2×/4× | `GET /recordings?cam=&tgl=&jam=` (halaman; HTMX: grid jam/menit), `GET /recordings/{cam}/{tgl}`, `GET /recordings/{cam}/{tgl}/{file}` |
| Ekspor | Pilih kamera + tanggal + jam mulai/selesai + nama pemohon (wajib). Ekspor berjalan di latar dan masuk Riwayat hari ini (jam, kamera, rentang, pemohon, ukuran, status Memproses/Siap/Gagal) dengan tombol Unduh atau Ulangi | `POST /export`, `GET /export`, `GET /export/{id}/download` |
| Kamera | Tabel kamera (cari nama/id/IP, filter online/offline; status, bitrate, restart, data terakhir), tambah/ubah/hapus lewat form, tombol uji per kamera | `GET /cameras`, `POST /cameras`, `PUT /cameras/{cam}`, `DELETE /cameras/{cam}`, `GET /cameras/{cam}/snapshot`, `POST /cameras/{cam}/test` |

**Uji kamera**: untuk kamera yang sedang online, uji memeriksa pipeline yang berjalan (login, data mengalir, fps, segmen terakhir) tanpa membuka koneksi baru, karena kamera V380 membatasi jumlah koneksi. Untuk kamera offline atau yang belum disimpan, uji mencoba login langsung seperti `--snapshot` di bagian Persiapan.

**Timeline**: endpoint timeline mengembalikan daftar menit yang punya rekaman, diambil dari nama file (`14-37-00.mp4` → `14:37`). Frontend menampilkan 24 tombol jam, masing-masing dengan jumlah menit yang terekam (jam yang punya lubang rekaman langsung terlihat dari jumlahnya), dan 60 kotak menit untuk jam yang dipilih; klik satu kotak memutar menit itu. Jam yang belum terjadi (atau setelah rekaman terakhir kamera offline) dinonaktifkan.

**Player sambung otomatis**: pakai dua elemen `<video>`. Satu memutar menit sekarang, satu lagi memuat menit berikutnya diam-diam; saat yang pertama selesai (`ended`), keduanya ditukar. Jeda antar menit jadi hampir tidak terasa.

**Snapshot kamera**: ambil dari segmen terbaru, bukan koneksi baru ke kamera:

```bash
ffmpeg -loglevel error -sseof -2 -i /recordings/igd-pintu/2026-10-18/14-37-00.mp4 \
  -frames:v 1 -q:v 3 -y snapshot.jpg
```

**`exporter.py`**: menggabungkan segmen 1 menit menjadi satu file tanpa re-encode.

```python
import asyncio, tempfile
from datetime import datetime, timedelta
from pathlib import Path

MAX_RANGE = timedelta(hours=2)

def segments(root: Path, cam: str, start: datetime, end: datetime):
    out, day = [], start.date()
    while day <= end.date():
        d = root / cam / day.isoformat()
        for f in sorted(d.glob("*.mp4")) if d.exists() else []:
            t = datetime.strptime(f"{day} {f.stem}", "%Y-%m-%d %H-%M-%S")
            if start - timedelta(minutes=1) < t < end:
                out.append(f)
        day += timedelta(days=1)
    return out

async def export_range(root: Path, cam: str, start: datetime, end: datetime) -> Path:
    if end <= start or end - start > MAX_RANGE:
        raise ValueError("rentang tidak valid (maksimal 2 jam)")
    files = segments(root, cam, start, end)
    if not files:
        raise FileNotFoundError("tidak ada rekaman di rentang itu")
    tmp = Path(tempfile.mkdtemp(prefix="ekspor-"))
    daftar = tmp / "daftar.txt"
    daftar.write_text("".join(f"file '{f}'\n" for f in files))
    out = tmp / f"{cam}_{start:%Y%m%d_%H%M}-{end:%H%M}.mp4"
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(daftar), "-c", "copy", "-tag:v", "hvc1",
        "-movflags", "+faststart", str(out))
    if await p.wait() != 0:
        raise RuntimeError("ekspor gagal")
    return out
```

**Ekspor sebagai antrean dengan riwayat.** Ekspor 2 jam bisa memakan waktu, jadi `POST /export` tidak menunggu hasil. Alurnya:

1. Validasi: kamera ada, rentang valid (maksimal 2 jam), dan **nama pemohon wajib diisi**. Gagal → 400 dengan pesan galat di atas.
2. Catat satu baris di tabel `exports` (`id`, `waktu`, `kamera`, `mulai`, `selesai`, `pemohon`, `status`, `ukuran`, `pesan`) dengan status `proses`, lalu kembalikan segera.
3. Background task memanggil `export_range`. Bila berhasil, file dipindahkan ke `/data/exports/<id>.mp4`, status `siap`, ukuran diisi. Bila gagal (mis. "tidak ada rekaman di rentang itu"), status `gagal` dan pesan disimpan.
4. `GET /export` mengembalikan riwayat hari ini untuk tabel Riwayat; `GET /export/{id}/download` mengirim file dengan `FileResponse`. Tombol Ulangi pada baris gagal hanya mengisi ulang formulir.
5. Cleaner ikut menghapus file di `/data/exports` yang lebih tua dari 24 jam (`GARDU_EXPORT_KEEP_HOURS`), karena file ekspor tidak ikut retensi rekaman. Baris riwayat tetap ada sebagai catatan.

Hasil ekspor tetap H.265; bila penerima tidak bisa memutarnya, sarankan VLC.

**Log audit.** Satu tabel `audit_log` di SQLite (`waktu`, `jenis`, `pelaku`, `keterangan`) melayani tiga tab Log di Dashboard lewat `GET /log/{jenis}` (`ekspor`, `login`, `kamera`; 50 baris terbaru):

| Jenis | Ditulis oleh | Pelaku | Contoh keterangan |
| --- | --- | --- | --- |
| `ekspor` | `api/export.py` | nama pemohon | `Ekspor IGD Lobby 13:40–14:00 (412 MB)`, atau `— gagal: tidak ada rekaman` |
| `login` | `auth.py` | IP klien | `Login berhasil`, `Login gagal (3×)` |
| `kamera` | `api/cameras.py` | IP klien | `Tambah kamera Gerbang Belakang`, `Ubah kamera Koridor Poli`, `Hapus kamera Apotek` |

Karena tanpa akun user, pelaku di log login dan perubahan kamera adalah alamat IP; hanya ekspor yang punya nama.

## Deployment

Gardu-recorder dan MediaMTX berjalan berdampingan dalam satu file Compose per lokasi; folder rekaman NAS di-mount ke `/recordings`.

**`Dockerfile`** (root)

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Jakarta PYTHONUNBUFFERED=1
WORKDIR /app
COPY gardu_recorder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gardu_recorder/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

`requirements.txt`: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `pydantic-settings`, `cryptography` (dipakai `v380_stream.py`).

**`docker-compose.yml`** (root)

```yaml
services:
  gardu-recorder:
    build: .
    restart: unless-stopped
    env_file: .env
    network_mode: host      # akses langsung ke kamera :8800 dan MediaMTX :8554
    volumes:
      - /volume2/gardu/recordings:/recordings
      - gardu-data:/data    # SQLite gardu.db (kamera, riwayat ekspor, log) + /data/exports; sertakan gardu.db dalam cadangan

  gardu-live:
    image: bluenviron/mediamtx:1.21.1
    restart: unless-stopped
    network_mode: host      # listener hanya 127.0.0.1 (lihat deploy/mediamtx.yml)
    environment:
      MTX_HLSCDNSECRET: ${GARDU_MEDIAMTX_HLS_SECRET:?isi GARDU_MEDIAMTX_HLS_SECRET di .env}
    volumes:
      - ./deploy/mediamtx.yml:/mediamtx.yml:ro

volumes:
  gardu-data:
```

Cadangkan volume `gardu-data` dan `GARDU_DB_KEY`: tanpa kunci itu, password kamera di database tidak bisa dibaca lagi.

Dengan `network_mode: host`, API gardu-recorder langsung terbuka di port 8080 host, dan `GARDU_MEDIAMTX_URL` di `.env` cukup `rtsp://127.0.0.1:8554`.

**Opsi lokasi menjalankan**

| Opsi | Kelebihan | Kekurangan |
| --- | --- | --- |
| Langsung di Synology (Container Manager) | Tulis ke disk lokal, tidak ada mount jaringan | CPU NAS terbatas untuk dekripsi banyak kamera, update lebih repot |
| VM/server terpisah + mount NFS ke NAS | Mudah dikelola, bisa banyak kamera | Bergantung pada jaringan ke NAS |

Bila memakai NFS, mount di host dengan opsi `hard` agar penulisan tidak gagal diam-diam saat NAS sesaat tidak terjangkau, dan pastikan UID container punya izin tulis ke shared folder.

## Tahapan pengerjaan

Kerjakan berurutan; setiap tahap sudah menghasilkan sesuatu yang bisa dipakai.

- [ ] **Tahap 0**: uji `--snapshot` dan stream 1 menit di semua kamera; catat Device ID, firmware, fps
- [ ] **Tahap 1**: pipeline 1 kamera: `v380_stream.py | ffmpeg` → segmen mp4 muncul di NAS tiap 1 menit
- [ ] **Tahap 2**: semua kamera (impor awal dari `GARDU_CAMERAS` di `.env`), watchdog + deteksi macet, cleaner
- [ ] **Tahap 3**: login satu password + menu Dashboard dan Kamera (SQLite, tambah/ubah/hapus, uji) + `audit_log` untuk login dan perubahan kamera
- [ ] **Tahap 4**: menu Rekaman: timeline + player sambung otomatis; uji pemutaran H.265 di browser petugas
- [ ] **Tahap 5**: menu Live View lewat MediaMTX + proxy HLS
- [ ] **Tahap 6**: menu Ekspor (antrean + riwayat + nama pemohon), log ekspor di Dashboard, uji beban 1 minggu

## Troubleshooting V380

| Gejala | Penyebab umum | Solusi |
| --- | --- | --- |
| Login gagal (`loginResult` bukan 1001) | Device ID, username, atau password salah | Cocokkan dengan data di aplikasi V380 Pro; username bisa berupa akun cloud |
| Login sukses tapi tidak ada data | Firmware bukan v31/v32, format paket berbeda | Cek versi firmware; metode ini hanya untuk v31/v32 |
| Gambar hijau/rusak | Dekripsi AES tidak cocok | Pastikan firmware sesuai; uji ulang dengan `--snapshot` |
| Durasi video terlalu cepat/lambat | `fps` di config tidak sesuai kamera | Sesuaikan `fps` dengan hasil `ffprobe` |
| Pipeline sering restart | Kamera reboot sendiri atau WiFi lemah | Watchdog + backoff sudah menangani; perbaiki sinyal atau pakai kabel |
| Tiba-tiba semua kamera gagal | Firmware ter-update otomatis | Matikan auto-update; cek apakah protokol berubah |
| Rekaman tidak bisa diputar di browser | Browser tidak mendukung H.265 | Pakai Safari/Edge dengan dekoder hardware, VLC, atau transcode on-demand |
| Stream rusak setelah beberapa detik | Ada log tercetak ke stdout | Pastikan log `v380_stream.py` hanya ke stderr (`RTSP_MODE=production`) |
| Jam di nama file salah | Zona waktu container UTC | Set `TZ=Asia/Jakarta` dan paket `tzdata` |
