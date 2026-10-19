import json
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta

import cleaner
import db
import exporter
import pytest
from fastapi.testclient import TestClient
from main import app

CAM = "ekspor-arsip"               # tidak ada di database: folder kamera yang sudah dihapus
YESTERDAY = date.today() - timedelta(days=1)

needs_x265 = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or "libx265" not in subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout,
    reason="butuh ffmpeg dengan libx265")


def form(**kw):
    data = {"cam": CAM, "tgl": YESTERDAY.isoformat(), "mulai": "10:00", "selesai": "10:03", "pemohon": "Siti – Satpam"}
    data.update(kw)
    return data


@pytest.fixture(scope="module")
def hevc_segment(tmp_path_factory):
    out = tmp_path_factory.mktemp("seg") / "seg.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "2",
                    "-c:v", "libx265", "-x265-params", "log-level=none", "-tag:v", "hvc1", str(out)], check=True)
    return out


@pytest.fixture
def cam_dir(tmp_root):
    d = tmp_root / "rec" / CAM / YESTERDAY.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def wait_status(client, export_id, timeout=20):
    for _ in range(timeout * 10):
        row = db.export_get(export_id)
        if row["status"] != "proses":
            return row
        time.sleep(0.1)
    raise AssertionError("ekspor tidak selesai")


def last_export_id():
    with db.connect() as conn:
        return conn.execute("SELECT MAX(id) FROM exports").fetchone()[0]


def test_requires_login(client):
    assert client.get("/export", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/export/1/download", follow_redirects=False).status_code == 303


def test_page_prefilled_from_query(logged, cam_dir):
    (cam_dir / "10-00-00.mp4").write_bytes(b"x" * 1000)
    del logged.headers["HX-Request"]
    r = logged.get(f"/export?cam={CAM}&tgl={YESTERDAY}&mulai=10:00&selesai=10:30")
    assert r.status_code == 200 and "<html" in r.text
    assert 'href="/export" class="on"' in r.text
    assert f'<option value="{CAM}" selected>' in r.text
    assert re.search(r'name="selesai_j".*?<option selected>10</option>', r.text, re.S)
    assert re.search(r'name="selesai_m".*?<option selected>30</option>', r.text, re.S)
    assert "1 file" in r.text and f"{CAM}_{YESTERDAY:%Y%m%d}_1000-1030.mp4" in r.text
    assert 'id="riwayat"' in r.text


def test_time_24h_formats(logged):
    r = logged.get("/export/ringkasan", params=form(mulai="9.05", selesai="0935"))
    assert "09:05–09:35 (30 mnt)" in r.text
    for bad in ("2:30 PM", "24:00", "14:60"):
        assert "format 24 jam" in logged.post("/export", data=form(mulai=bad)).text


def test_time_from_dropdowns(logged):
    params = {k: v for k, v in form().items() if k not in ("mulai", "selesai")}
    params.update(mulai_j="09", mulai_m="05", selesai_j="09", selesai_m="35")
    assert "09:05–09:35 (30 mnt)" in logged.get("/export/ringkasan", params=params).text
    r = logged.post("/export", data={**params, "selesai_j": "25"})
    assert r.status_code == 422 and "format 24 jam" in r.text


def test_summary_fragment(logged, cam_dir):
    r = logged.get("/export/ringkasan", params=form(selesai="13:00"))      # galat baru muncul saat dikirim
    assert "maksimal 2 jam" not in r.text and "⚠" not in r.text and "Segmen<b>—" in r.text
    r = logged.get("/export/ringkasan", params=form(mulai="23:30", selesai="00:30"))
    assert "23:30–00:30 (besok) (60 mnt)" in r.text


@pytest.mark.parametrize("kw, msg", [
    ({"pemohon": "  "}, "Nama pemohon wajib diisi"),
    ({"selesai": "12:30"}, "maksimal 2 jam"),
    ({"selesai": "10:00"}, "Jam selesai harus setelah jam mulai"),
    ({"cam": "tidak-ada"}, "Pilih kamera"),
    ({"tgl": (date.today() + timedelta(days=1)).isoformat()}, "masa simpan"),
    ({"tgl": (date.today() - timedelta(days=30)).isoformat()}, "masa simpan"),
    ({"tgl": date.today().isoformat(), "mulai": "23:58", "selesai": "23:59"}, "belum lewat"),
    ({"mulai": "bukan-jam"}, "Isi tanggal"),
])
def test_create_validation(logged, kw, msg):
    if msg == "belum lewat" and datetime.now().strftime("%H:%M") >= "23:59":
        pytest.skip("menit terakhir hari ini")
    before = last_export_id()
    r = logged.post("/export", data=form(**kw))
    assert r.status_code == 422 and msg in r.text and r.text.startswith('<form id="x-form"')
    assert last_export_id() == before


def test_segments_selection(tmp_path):
    day = date(2026, 10, 18)
    for d, names in ((day, ["22-58-00.mp4", "23-59-00.mp4", "23-59-40.mp4", "x.mp4"]),
                     (day + timedelta(days=1), ["00-00-00.mp4", "00-01-00.mp4", "00-02-00.mp4"])):
        p = tmp_path / "cam" / d.isoformat()
        p.mkdir(parents=True)
        for n in names:
            (p / n).write_bytes(b"x")
    (tmp_path / "cam" / "2026-10-19" / "00-01-00.mp4").write_bytes(b"")          # kosong: dilewati
    start, end = datetime(2026, 10, 18, 23, 59), datetime(2026, 10, 19, 0, 2)
    got = exporter.segments(tmp_path, "cam", start, end, 60, now=datetime(2026, 10, 20))
    assert [f"{f.parent.name}/{f.name}" for f in got] == [
        "2026-10-18/23-59-00.mp4", "2026-10-18/23-59-40.mp4", "2026-10-19/00-00-00.mp4"]
    # pukul 00:00:30: 00-00-00 masih ditulis (terbaru, durasi belum lewat) → tidak ikut; 23-59-40 (restart di
    # tengah menit) sudah diikuti segmen lain → selesai meski 60 detiknya belum lewat
    for n in ("00-01-00.mp4", "00-02-00.mp4"):
        (tmp_path / "cam" / "2026-10-19" / n).unlink()
    got = exporter.segments(tmp_path, "cam", start, end, 60, now=datetime(2026, 10, 19, 0, 0, 30))
    assert [f.name for f in got] == ["23-59-00.mp4", "23-59-40.mp4"]


@needs_x265
def test_export_flow_and_download(logged, cam_dir, hevc_segment):
    for n in ("10-00-00.mp4", "10-01-00.mp4", "10-02-00.mp4", "10-05-00.mp4"):
        shutil.copy(hevc_segment, cam_dir / n)
    r = logged.post("/export", data=form())
    assert r.status_code == 200 and "masuk antrean" in r.text and 'hx-swap-oob' in r.text
    assert r.headers["HX-Trigger"] == "export-added"
    export_id = last_export_id()
    row = wait_status(logged, export_id)
    assert row["status"] == "siap" and row["ukuran"] > 0 and row["pemohon"] == "Siti – Satpam"

    hist = logged.get("/export").text                      # HTMX: hanya tabel riwayat
    assert hist.startswith('<div id="riwayat"') and f'/export/{export_id}/download' in hist
    assert "every 3s" not in hist

    r = logged.get(f"/export/{export_id}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert f'{CAM}_{YESTERDAY:%Y%m%d}_1000-1003.mp4' in r.headers["content-disposition"]
    out = cam_dir.parent / "hasil.mp4"
    out.write_bytes(r.content)
    if shutil.which("ffprobe"):
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_tag_string",
                                "-of", "json", str(out)], capture_output=True, text=True, check=True)
        info = json.loads(probe.stdout)
        assert 5.5 < float(info["format"]["duration"]) < 6.5                  # 3 segmen × 2 detik
        assert info["streams"][0]["codec_tag_string"] == "hvc1"
    else:                                                  # ffmpeg -i mencetak durasi ke stderr
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
        assert re.search(r"Duration: 00:00:0(5\.[5-9]|6\.[0-4])", err) and "hvc1" in err
    out.unlink()

    log = logged.get("/log/ekspor").text
    assert f"Ekspor {CAM} (dihapus) {YESTERDAY.day}" in log and "10:00–10:03" in log and "Siti – Satpam" in log


def test_export_without_recordings_fails_with_retry(logged, cam_dir):
    logged.post("/export", data=form(mulai="03:00", selesai="03:10"))
    row = wait_status(logged, last_export_id())
    assert row["status"] == "gagal" and row["pesan"] == "tidak ada rekaman di rentang itu"
    hist = logged.get("/export").text
    assert re.search(r'href="/export\?cam=ekspor-arsip&amp;tgl=[\d-]+&amp;mulai=03%3A00&amp;selesai=03%3A10', hist)
    assert "gagal: tidak ada rekaman" in logged.get("/log/ekspor").text
    assert logged.get(f"/export/{row['id']}/download").status_code == 404


def test_download_unknown(logged):
    r = logged.get("/export/999999/download")
    assert r.status_code == 404 and "tidak tersedia" in r.text


def test_running_exports_aborted_on_start(client):
    export_id = db.export_create(CAM, CAM, "2026-10-18 10:00", "2026-10-18 10:05", "Uji", "127.0.0.1")
    with TestClient(app):
        pass
    row = db.export_get(export_id)
    assert row["status"] == "gagal" and "dimulai ulang" in row["pesan"]


def test_cleaner_removes_old_exports(tmp_path):
    import os
    old, new = tmp_path / "1.mp4", tmp_path / "2.mp4"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (time.time() - 25 * 3600,) * 2)
    cleaner.clean_exports(tmp_path, keep_hours=24)
    assert not old.exists() and new.exists()


def test_history_hides_download_when_file_cleaned(logged):
    export_id = db.export_create(CAM, CAM, f"{date.today()} 00:00", f"{date.today()} 00:05", "Uji", "127.0.0.1")
    db.export_finish(export_id, "siap", 1234)          # tanpa file: sudah dihapus cleaner
    hist = logged.get("/export").text
    assert f"/export/{export_id}/download" not in hist and "file dihapus otomatis" in hist
