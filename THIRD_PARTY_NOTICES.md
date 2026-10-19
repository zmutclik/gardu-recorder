# Komponen Pihak Ketiga

Gardu berlisensi MIT (`LICENSE`). Komponen berikut disertakan di repo ini dengan lisensinya masing-masing:

| Komponen | Versi | Lokasi | Lisensi | Teks lisensi |
|---|---|---|---|---|
| [hls.js](https://github.com/video-dev/hls.js) | 1.7.3 | `gardu_recorder/web/static/js/vendor/hls.min.js` | Apache-2.0 | `gardu_recorder/web/static/js/vendor/LICENSE-hls.js.txt` |
| [htmx](https://github.com/bigskysoftware/htmx) | 2.0.4 | `gardu_recorder/web/static/js/vendor/htmx.min.js` | 0BSD | `gardu_recorder/web/static/js/vendor/LICENSE-htmx.txt` |
| [v380_stream](https://github.com/zmutclik/v380_stream) | — | `gardu_recorder/vendor/v380_stream.py` | MIT | `gardu_recorder/vendor/LICENSE` |

Tidak disertakan di repo, diunduh/dipasang saat build atau dijalankan:

- [MediaMTX](https://github.com/bluenviron/mediamtx) (MIT): image Docker `gardu-live`, atau diunduh `start.sh` ke `.tools/`.
- [FFmpeg](https://ffmpeg.org/) (LGPL/GPL, tergantung build): dipasang dari paket distro di `Dockerfile`, dipanggil sebagai proses terpisah.
- Paket Python di `gardu_recorder/requirements.txt` (FastAPI, Uvicorn, Jinja2, Pydantic, cryptography, dll.) dengan lisensi masing-masing.
