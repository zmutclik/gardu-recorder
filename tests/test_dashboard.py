import cleaner
import db


def test_dashboard_renders_and_toggles_auto(logged):
    r = logged.get("/dashboard")                     # HX-Request → hanya fragment
    assert 'id="stats"' in r.text and "<html" not in r.text and "HX-Request" in r.headers["Vary"]
    full = logged.get("/dashboard", headers={"HX-Request": ""})
    assert "<html" in full.text and 'id="log"' in full.text
    on = logged.get("/dashboard/stats?auto=1").text
    off = logged.get("/dashboard/stats?auto=0").text
    assert 'hx-trigger="every 10s"' in on and 'hx-trigger="every 10s"' not in off


def test_log_tabs(logged):
    db.audit("kamera", "10.0.0.1", "Tambah kamera Uji")
    assert "Tambah kamera Uji" in logged.get("/log/kamera").text
    assert logged.get("/log/ekspor").status_code == 200
    assert logged.get("/log/lain").status_code == 404


def test_cleaner_removes_old_days_keeps_today(tmp_path):
    from datetime import date, timedelta
    for delta in (20, 14, 13, 0):                     # retensi 14 hari = hari ini + 13 hari sebelumnya
        d = tmp_path / "cam" / (date.today() - timedelta(days=delta)).isoformat()
        d.mkdir(parents=True)
        (d / "00-00-00.mp4").write_bytes(b"x" * 10)
    cleaner.clean_once(tmp_path, retention_days=14, max_pct=99)
    left = sorted(p.name for p in (tmp_path / "cam").iterdir())
    assert left == sorted([(date.today() - timedelta(days=13)).isoformat(), date.today().isoformat()])
    assert cleaner.recordings_bytes == 20


def test_max_safe_waits_for_recording_size(monkeypatch):
    from types import SimpleNamespace

    import stats
    rec = SimpleNamespace(state="online", mbps=8.0, cam=SimpleNamespace(name="A"))
    manager = SimpleNamespace(recs={"a": rec})
    monkeypatch.setattr(cleaner, "recordings_bytes", None)
    s = stats.collect(manager)
    assert s["max_safe_days"] is None and s["sizing"]       # belum dihitung: jangan tebak terlalu rendah
    monkeypatch.setattr(cleaner, "recordings_bytes", 0)
    s = stats.collect(manager)
    if s["disk"]:
        assert s["max_safe_days"] is not None and not s["sizing"]
