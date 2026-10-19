# Design Context — Gardu (NVR V380)

Generated: 2026-10-18

Sumber: `panduan_pembuatan_gardu-recorder.md` (belum ada kode/UI, jadi konteks diambil dari panduan; tanpa screenshot).

## App Overview
Gardu adalah NVR self-hosted untuk kamera CCTV V380. Recorder menyimpan segmen mp4 1 menit ke NAS, MediaMTX menyajikan live view (HLS lewat proxy FastAPI, jeda 2–6 detik). Dipakai petugas (mis. IGD rumah sakit) lewat web dengan satu password, tanpa akun user.

## Target Platform
Desktop / Web (PC petugas). FastAPI + Jinja2 + sedikit JS.

## Layout Patterns
- Halaman server-rendered, lima menu setelah login
- Grid kamera 1/4/9 untuk live view

## Navigation
- Menu: Dashboard, Live View, Rekaman, Ekspor, Kamera
- Logout; tanpa nama user

## Page Types
### Live View
- Structure: grid 1/4/9 kamera; klik satu kamera → layar penuh
- Key elements: stream HLS per kamera (`/live/{cam}/`), nama kamera, status online/offline
### Rekaman
- Pilih kamera + tanggal, timeline 24 jam (1.440 kotak), player sambung otomatis, kecepatan 1×/2×/4×
### Ekspor
- Pilih kamera + jam mulai/selesai (maks 2 jam), `POST /export`, hasil satu mp4 (H.265, tanpa re-encode)
- Galat: "rentang tidak valid (maksimal 2 jam)", "tidak ada rekaman di rentang itu", "ekspor gagal"
- Privasi: tanpa akun user, jadi nama pemohon diisi saat ekspor dan dicatat ke log
### Kamera
- Data kamera di SQLite, dikelola dari web: tambah, ubah, hapus (konfirmasi hapus: rekaman lama di NAS tidak ikut terhapus)
- Form kamera: nama, IP, Device ID, username, password (terenkripsi, tidak pernah ditampilkan; kosong saat ubah = tidak diubah), kualitas, fps; id dari slug nama; Uji koneksi sebelum Simpan
- Daftar: snapshot terbaru (dari segmen, bukan koneksi baru), status online/offline, bitrate, restart, data terakhir; tombol uji
- Uji kamera online memakai pipeline berjalan (batas koneksi kamera V380); kamera offline/baru boleh login langsung
- Simpan/ubah/hapus hanya menyentuh pipeline kamera itu
### Dashboard
- Halaman pertama setelah login; sumber: `GET /health`, `GET /cameras`
- Status tiap kamera (online/offline, restart, bitrate, data terakhir), pemakaian disk NAS, retensi + maksimum aman
- Ringkasan pengaturan hanya tampil (live view, keamanan & ekspor, aplikasi) dan log (ekspor, login, perubahan kamera); tidak ada menu Pengaturan. Retensi/batas disk diubah lewat `cameras.yaml`/`.env`
- Peringatan yang perlu tindakan: kamera offline, fps tidak sesuai, restart berulang, cadangan belum ada, disk dekat batas

## Interaction Patterns
- Klik kotak timeline memutar menit itu
- Kamera offline harus jelas terlihat (watchdog restart dengan backoff)

## Content Hierarchy
- Kamera: id (`igd-pintu`), nama ("IGD Pintu Masuk"), IP, status
- Bahasa UI: Indonesia

## UX Conventions
- Satu koneksi per kamera dipakai bersama rekam dan live; hindari desain yang membuka banyak stream sekaligus tanpa perlu
- Live HLS punya jeda 2–6 detik; tampilkan penanda "LIVE"/jeda
- H.265 tidak selalu diputar di browser; perlu fallback/pesan jelas
