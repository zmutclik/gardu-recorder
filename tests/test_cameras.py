import shutil
import sqlite3

import db
from config import storage

FORM = {"name": "IGD Lobby", "ip": "192.168.10.52", "port": "8800", "device_id": "23456789",
        "username": "admin", "password": "rahasia-kamera", "quality": "1", "fps": "16"}


def _reset(client):
    """Kamera dihapus lewat API (pipeline berhenti) beserta folder rekamannya, termasuk sisa test sebelumnya:
    tanpa itu kamera baru bernama sama mendapat ID berakhiran (-2)."""
    for cam in db.list_cameras():
        client.delete(f"/cameras/{cam.id}")
    for d in [*storage.path.glob("igd-lobby*"), *storage.path.glob("gudang*")]:
        shutil.rmtree(d, ignore_errors=True)


def test_create_validates_and_returns_form_422(logged):
    _reset(logged)
    r = logged.post("/cameras", data={**FORM, "ip": "bukan-ip", "device_id": "abc"})
    assert r.status_code == 422
    assert r.headers["HX-Retarget"] == "#modal"
    assert "bukan alamat IP" in r.text and "Device ID harus berupa angka" in r.text
    assert "rahasia-kamera" not in r.text


def test_create_edit_delete_flow(logged):
    _reset(logged)
    r = logged.post("/cameras", data=FORM)
    assert r.status_code == 200 and "camera-saved" in r.headers["HX-Trigger"]
    assert "Kamera IGD Lobby disimpan" in r.text
    cam = db.get_camera("igd-lobby")
    assert cam.password == "rahasia-kamera"
    assert "igd-lobby" in logged.app.state.manager.recs
    assert "Tambah kamera IGD Lobby" in db.recent_logs("kamera")[0]["keterangan"]

    # password di database terenkripsi, dan tidak pernah dikirim ke browser
    raw = sqlite3.connect(db.DB_PATH).execute("SELECT password_enc FROM cameras").fetchone()[0]
    assert "rahasia-kamera" not in raw
    assert "rahasia-kamera" not in logged.get("/cameras/igd-lobby/edit").text
    assert "rahasia-kamera" not in logged.get("/cameras/table").text

    # ubah: password kosong = tidak diubah; fps berubah → pipeline restart
    old = logged.app.state.manager.recs["igd-lobby"]
    r = logged.put("/cameras/igd-lobby", data={**FORM, "password": "", "fps": "25"})
    assert r.status_code == 200 and "dimulai ulang" in r.text
    assert db.get_camera("igd-lobby").password == "rahasia-kamera" and db.get_camera("igd-lobby").fps == 25
    assert logged.app.state.manager.recs["igd-lobby"] is not old
    assert "fps 25" in db.recent_logs("kamera")[0]["keterangan"]

    # hanya nama berubah → pipeline tidak dimulai ulang
    rec = logged.app.state.manager.recs["igd-lobby"]
    r = logged.put("/cameras/igd-lobby", data={**FORM, "password": "", "fps": "25", "name": "IGD Lobi"})
    assert "dimulai ulang" not in r.text and logged.app.state.manager.recs["igd-lobby"] is rec
    assert rec.cam.name == "IGD Lobi"

    r = logged.delete("/cameras/igd-lobby")
    assert r.status_code == 200 and db.get_camera("igd-lobby") is None
    assert "igd-lobby" not in logged.app.state.manager.recs
    assert "Hapus kamera IGD Lobi" in db.recent_logs("kamera")[0]["keterangan"]


def test_duplicate_ip_and_name(logged):
    _reset(logged)
    assert logged.post("/cameras", data=FORM).status_code == 200
    r = logged.post("/cameras", data={**FORM, "name": "Lain"})
    assert r.status_code == 422 and "sudah dipakai" in r.text
    r = logged.post("/cameras", data={**FORM, "ip": "192.168.10.99"})      # nama sama, IP lain: ID diberi akhiran
    assert r.status_code == 200 and db.get_camera("igd-lobby-2").ip == "192.168.10.99"


def test_new_camera_does_not_reuse_deleted_camera_folder(logged, tmp_root):
    _reset(logged)
    (tmp_root / "rec" / "gudang" / "2026-01-01").mkdir(parents=True, exist_ok=True)   # rekaman kamera yang dihapus
    assert logged.post("/cameras", data={**FORM, "name": "Gudang"}).status_code == 200
    assert db.get_camera("gudang") is None and db.get_camera("gudang-2").name == "Gudang"


def test_free_id_keeps_max_length():
    from api.cameras import free_id
    assert free_id("a" * 40) == "a" * 40


def test_add_requires_password_and_sluggable_name(logged):
    _reset(logged)
    assert "Password wajib diisi" in logged.post("/cameras", data={**FORM, "password": ""}).text
    assert "huruf atau angka" in logged.post("/cameras", data={**FORM, "name": "!!!"}).text


def test_table_filters_and_unknown_camera(logged):
    _reset(logged)
    logged.post("/cameras", data=FORM)
    assert "IGD Lobby" in logged.get("/cameras/table?q=lobby").text
    assert "Tidak ada kamera yang cocok" in logged.get("/cameras/table?q=zzz").text
    assert logged.get("/cameras/nope/edit").status_code == 404
    assert logged.delete("/cameras/nope").status_code == 404


def test_test_endpoint_unreachable_port(logged):
    _reset(logged)
    r = logged.post("/cameras/test", data={**FORM, "ip": "127.0.0.1", "port": "1"})
    assert r.status_code == 200 and "Gagal di: Port 1 terbuka" in r.text


def test_direct_login_with_non_camera_listener_fails_at_login(logged):
    """Port terbuka tapi yang menjawab bukan kamera V380 (menutup koneksi): gagal di Login, bukan di Data video."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def accept_and_close():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.close()

    threading.Thread(target=accept_and_close, daemon=True).start()
    _reset(logged)
    r = logged.post("/cameras/test", data={**FORM, "ip": "127.0.0.1", "port": str(srv.getsockname()[1])})
    srv.close()
    assert "Gagal di: Login" in r.text and "bukan kamera V380" in r.text


def test_saved_password_not_sent_to_changed_target(logged, monkeypatch):
    """Password tersimpan tidak boleh dikirim ke IP/port/akun lain (penerimanya bisa membaca password V380)."""
    import tester
    _reset(logged)
    assert logged.post("/cameras", data=FORM).status_code == 200
    calls = []

    async def fake_run_test(ip, port, device_id, username, password, quality, rec):
        calls.append((ip, password))
        return "uji", []

    monkeypatch.setattr(tester, "run_test", fake_run_test)
    r = logged.post("/cameras/test", data={**FORM, "cam_id": "igd-lobby", "password": "", "ip": "192.168.99.9"})
    assert "isi ulang password" in r.text and calls == []
    logged.post("/cameras/test", data={**FORM, "cam_id": "igd-lobby", "password": ""})
    assert calls == [("192.168.10.52", "rahasia-kamera")]            # tujuan sama: password tersimpan boleh

    r = logged.put("/cameras/igd-lobby", data={**FORM, "password": "", "port": "8801"})
    assert r.status_code == 422 and "isi ulang password" in r.text
    assert db.get_camera("igd-lobby").port == 8800
    r = logged.put("/cameras/igd-lobby", data={**FORM, "password": "baru", "ip": "192.168.10.60"})
    assert r.status_code == 200 and db.get_camera("igd-lobby").ip == "192.168.10.60"
    _reset(logged)


def test_camera_ip_must_be_in_camera_networks(logged):
    """Server tidak bisa disuruh menghubungi alamat di luar GARDU_CAMERA_NETWORKS (mis. internet)."""
    _reset(logged)
    r = logged.post("/cameras/test", data={**FORM, "ip": "8.8.8.8"})
    assert "di luar jaringan kamera" in r.text
    r = logged.post("/cameras", data={**FORM, "ip": "10.1.2.3"})
    assert r.status_code == 422 and "di luar jaringan kamera" in r.text
    assert db.list_cameras() == []


def test_status_filter_separates_connecting_and_offline(logged):
    from config import LiveConfig, StorageConfig
    from db import Camera
    from recorder import CameraRecorder
    manager = logged.app.state.manager

    def fake(cam_id):
        cam = Camera(id=cam_id, name=cam_id, ip="192.168.77.1", port=8800, device_id=1, username="u",
                     quality=1, fps=16, password="p")
        return CameraRecorder(cam, StorageConfig(path="/tmp"), LiveConfig())

    baru, mati = fake("filter-baru"), fake("filter-mati")
    mati.restarts = 1                                  # sudah pernah gagal → offline
    manager.recs.update({"filter-baru": baru, "filter-mati": mati})
    try:
        off = logged.get("/cameras/table?status=offline").text
        conn = logged.get("/cameras/table?status=connecting").text
        assert "filter-mati" in off and "filter-baru" not in off
        assert "filter-baru" in conn and "filter-mati" not in conn
    finally:
        manager.recs.pop("filter-baru")
        manager.recs.pop("filter-mati")
