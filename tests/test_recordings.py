from datetime import date, timedelta

import pytest

CAM = "arsip"                      # tidak ada di database: folder kamera yang sudah dihapus
YESTERDAY = date.today() - timedelta(days=1)


@pytest.fixture
def day_dir(tmp_root):
    d = tmp_root / "rec" / CAM / YESTERDAY.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    for name in ("09-00-00.mp4", "14-37-00.mp4", "14-38-00.mp4", "14-38-31.mp4"):   # 14-38-31: restart di tengah menit
        (d / name).write_bytes(b"0123456789" * 10)
    (d / "15-00-00.mp4").write_bytes(b"")          # baru dibuka ffmpeg: belum ada data
    (d / "catatan.txt").write_text("bukan segmen")
    return d


def test_requires_login(client):
    r = client.get("/recordings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get(f"/recordings/{CAM}/{YESTERDAY}", follow_redirects=False).status_code == 303


def test_page_shows_hours_and_minutes(logged, day_dir):
    del logged.headers["HX-Request"]
    r = logged.get(f"/recordings?cam={CAM}&tgl={YESTERDAY}")
    assert r.status_code == 200 and "<html" in r.text
    assert "arsip (dihapus)" in r.text
    assert "Jam 14:00–14:59" in r.text                 # default: jam segmen terakhir
    assert "14<small>2 mnt</small>" in r.text and "09<small>1 mnt</small>" in r.text
    assert "15<small>0 mnt</small>" in r.text          # file 0 byte tidak dihitung
    assert 'data-t="14:37"' in r.text and 'data-t="14:38"' in r.text and 'data-t="14:39"' not in r.text
    assert "rekaman.js" in r.text


def test_hour_fragment_for_htmx(logged, day_dir):
    r = logged.get(f"/recordings?cam={CAM}&tgl={YESTERDAY}&jam=9")
    assert r.status_code == 200 and "<html" not in r.text
    assert r.text.startswith('<div id="timeline" data-jam="9">')
    assert 'data-t="09:00"' in r.text and "Jam 09:00–09:59" in r.text


def test_future_day_and_unknown_camera(logged, day_dir):
    r = logged.get(f"/recordings?cam={CAM}&tgl={date.today() + timedelta(days=1)}")
    assert r.text.count('class="btn dis"') == 24 and "data-t=" not in r.text
    assert logged.get("/recordings?cam=tidak-ada").status_code == 404


def test_list_day_json(logged, day_dir):
    r = logged.get(f"/recordings/{CAM}/{YESTERDAY}")
    assert r.status_code == 200
    assert [s["file"] for s in r.json()] == ["09-00-00.mp4", "14-37-00.mp4", "14-38-00.mp4", "14-38-31.mp4"]
    assert r.json()[1] == {"file": "14-37-00.mp4", "t": "14:37", "size": 100}
    assert logged.get(f"/recordings/{CAM}/{date.today() - timedelta(days=30)}").json() == []


def test_serve_segment_with_range_and_download(logged, day_dir):
    url = f"/recordings/{CAM}/{YESTERDAY}/14-37-00.mp4"
    r = logged.get(url, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"0123"
    assert r.headers["content-type"] == "video/mp4" and r.headers["cache-control"] == "private, no-cache"
    assert "content-disposition" not in r.headers
    r = logged.get(url + "?unduh=1")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="{CAM}_{YESTERDAY}_14-37-00.mp4"'
    assert logged.get(f"/recordings/{CAM}/{YESTERDAY}/10-00-00.mp4").status_code == 404


@pytest.mark.parametrize("path", [
    f"/recordings/{CAM}/{YESTERDAY}/catatan.txt",
    f"/recordings/{CAM}/{YESTERDAY}/..%2F..%2F..%2Fdata%2Fgardu.db",
    f"/recordings/{CAM}/bukan-tanggal",
    f"/recordings/..%2F..%2Fetc/{YESTERDAY}",
    "/recordings/ARSIP/2026-10-18",
])
def test_rejects_paths_outside_recordings(logged, day_dir, path):
    assert logged.get(path).status_code in (404, 422)


def test_deleted_camera_without_recordings_not_offered(tmp_root):
    from api.recordings import camera_choices
    (tmp_root / "rec" / "kosong" / "2026-01-01").mkdir(parents=True, exist_ok=True)   # sisa folder setelah retensi
    assert "kosong" not in {c["id"] for c in camera_choices()}


def test_player_continues_to_next_day_until_today(logged, day_dir):
    del logged.headers["HX-Request"]
    past = logged.get(f"/recordings?cam={CAM}&tgl={YESTERDAY}").text
    assert f'data-next="/recordings?cam={CAM}&amp;tgl={date.today()}"' in past
    assert 'data-next=""' in logged.get(f"/recordings?cam={CAM}&tgl={date.today()}").text


def test_retention_counts_today(logged, day_dir):
    from config import storage
    del logged.headers["HX-Request"]
    oldest = date.today() - timedelta(days=storage.retention_days - 1)
    assert f'min="{oldest}"' in logged.get(f"/recordings?cam={CAM}").text
