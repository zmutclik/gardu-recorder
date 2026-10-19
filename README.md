# Gardu (NVR V380)

Rekam + web untuk kamera V380 (protokol asli, port 8800).

**Status**: tahap 1–6 (pipeline rekam, watchdog, cleaner, login, Dashboard, Kamera, audit log, Rekaman, Live View,
Ekspor) selesai dan teruji dengan sumber palsu; live view juga diuji dengan MediaMTX v1.21.1 sungguhan. Belum diuji:
pemutaran H.265 (rekaman dan live) di browser PC petugas, uji beban 1 minggu. **Tahap 0 (uji kamera sungguhan) belum
dilakukan.**

## Jalankan (dev)

Paling mudah: `./start.sh` (hanya 127.0.0.1; `GARDU_HOST=0.0.0.0 ./start.sh` untuk dibuka dari LAN) (venv, `.env` dev, MediaMTX v1.21.1 di `.tools/` untuk live view, lalu uvicorn di :8035).
Manual:

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements-dev.txt
cp .env.example .env   # satu-satunya .env, di root: semua konfigurasi (rahasia, storage, live, impor kamera)
cat >> .env <<EOT
GARDU_DATA_DIR=$PWD/data
GARDU_STORAGE_PATH=$PWD/recordings
DEBUG=true
COOKIE_SECURE=false
EOT
cd gardu_recorder && ../.venv/bin/uvicorn main:app --reload
```

## Test

```bash
.venv/bin/pytest          # test recorder butuh ffmpeg + libx265 (sumber H.265 palsu), dilewati bila tak ada
.venv/bin/ruff check gardu_recorder tests
```

## Produksi

`docker compose up -d` dari root (`docker-compose.yml` + `Dockerfile` di root, `.env` yang sama dari `.env.example`).
Kode `gardu_recorder/` di-mount ke container (tidak disalin ke image), jadi update cukup `git pull && docker compose
restart gardu-recorder`; `docker compose build` hanya bila `requirements.txt` atau `Dockerfile` berubah. Cadangkan volume `gardu-data` **dan**
`GARDU_DB_KEY`: tanpa kunci itu password kamera di database tidak bisa dibaca lagi. Container berjalan sebagai
user non-root `GARDU_UID`/`GARDU_GID` (.env): samakan dengan pemilik folder rekaman di NAS. Volume `gardu-data`
yang dibuat image lama (milik root) perlu `chown` sekali ke UID itu.

Akses web: `COOKIE_SECURE=auto` (bawaan) memasang cookie Secure bila dibuka lewat https (reverse proxy TLS di depan
`127.0.0.1:8080`) dan cookie biasa bila dibuka langsung lewat `http://<ip>:8080` (LAN saja: tidak terenkripsi).
`COOKIE_SECURE=true` memaksa https.
Proxy di mesin lain: isi `FORWARDED_ALLOW_IPS` dengan IP-nya, kalau tidak semua petugas tampak satu IP dan berbagi
kunci percobaan login. Lihat `.env.example`.

## Catatan desain

- `gardu_recorder/vendor/` = salinan `zmutclik/v380_stream` (MIT, lisensi ikut). Jangan diformat/di-lint.
- Live view: MediaMTX hanya listen di 127.0.0.1, publish hanya user `gardu` (password = `GARDU_MEDIAMTX_HLS_SECRET`); Gardu meneruskan HLS `/live/<cam>/…` setelah login dengan Bearer
  `GARDU_MEDIAMTX_HLS_SECRET` (= `hlsCDNSecret` MediaMTX). `web/static/js/vendor/hls.min.js` = hls.js 1.7.3.
- Ekspor: satu per satu di latar (`exporter.ExportQueue`, maks. 5 antre), `ffmpeg -f concat -c copy` ke
  `<GARDU_DATA_DIR>/exports/<id>.mp4`; segmen yang masih ditulis ffmpeg tidak ikut. Jam = jam lokal server (nama
  segmen); jam selesai ≤ jam mulai = melewati tengah malam. Ekspor yang sedang berjalan saat aplikasi berhenti
  ditandai gagal saat start berikutnya (tombol Ulangi).
- Frontend: Jinja2 + htmx (tanpa Alpine sampai ada yang memerlukannya); CSP ketat tanpa script/style inline.
- Auth: satu password (plain text di .env) + cookie sesi 12 jam (juga dicatat di tabel `sessions`: logout mencabut
  sesi, ganti `GARDU_PASSWORD` membatalkan semua sesi) + CSRF di semua non-GET. Kunci percobaan login per IP disimpan
  di memori proses: reset saat restart, dan hanya benar untuk satu worker uvicorn.

## Lisensi

MIT, lihat `LICENSE`. Komponen pihak ketiga (hls.js, htmx, v380_stream, MediaMTX, FFmpeg) dan lisensinya: `THIRD_PARTY_NOTICES.md`.
