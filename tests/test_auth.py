import auth
import db
import pytest
from conftest import PASSWORD, csrf_from
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_protected_pages_redirect_to_login(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = client.get("/cameras", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"


def test_health_is_public_and_minimal(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_login_wrong_password_is_audited(client):
    r = client.post("/login", data={"password": "salah", "csrf_token": csrf_from(client)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?salah=1"
    assert "Login gagal" in db.recent_logs("login")[0]["keterangan"]


def test_login_requires_csrf(client):
    csrf_from(client)
    r = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/login", data={"password": PASSWORD, "csrf_token": "salah"}, follow_redirects=False)
    assert r.status_code == 403


def test_lockout_after_five_failures(client):
    token = csrf_from(client)
    for _ in range(5):
        client.post("/login", data={"password": "salah", "csrf_token": token})
    r = client.post("/login", data={"password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 429


def test_login_ok_then_pages_and_logout(logged):
    assert logged.get("/dashboard").status_code == 200
    assert "Login berhasil" in db.recent_logs("login")[0]["keterangan"]
    r = logged.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert logged.get("/dashboard", follow_redirects=False).status_code == 401   # klien ini membawa HX-Request
    assert logged.get("/dashboard", headers={"HX-Request": ""}, follow_redirects=False).status_code == 303


def test_non_get_without_csrf_is_rejected(logged):
    del logged.headers["X-CSRF-Token"]
    assert logged.post("/cameras", data={"name": "x"}).status_code == 403


def test_security_headers_and_no_store(client):
    r = client.get("/login")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/static/js/app.js").status_code == 200


def test_logout_revokes_copied_cookie(logged):
    """Cookie sesi yang disalin (mis. dicuri) tidak berlaku lagi setelah pemiliknya logout."""
    stolen = logged.cookies.get("gardu_session")
    assert logged.post("/logout", follow_redirects=False).status_code == 303
    logged.cookies.set("gardu_session", stolen)
    assert logged.get("/dashboard", follow_redirects=False).status_code == 401


def test_password_change_invalidates_sessions(logged, monkeypatch):
    import auth
    assert logged.get("/dashboard").status_code == 200
    monkeypatch.setattr(auth, "PW_FINGERPRINT", "sidik-password-baru")
    assert logged.get("/dashboard", follow_redirects=False).status_code == 401


def test_short_password_rejected_outside_debug(monkeypatch):
    import pytest
    from config import Settings
    from pydantic import ValidationError
    monkeypatch.setenv("GARDU_PASSWORD", "2244")
    with pytest.raises(ValidationError, match="minimal 12"):
        Settings(_env_file=None, DEBUG=False)
    assert Settings(_env_file=None, DEBUG=True).GARDU_PASSWORD == "2244"


def test_lockout_is_atomic_under_concurrency(client):
    """Percobaan paralel tidak boleh lolos melewati batas MAX_FAIL (cek+catat dalam satu kunci)."""
    from concurrent.futures import ThreadPoolExecutor

    import auth
    token = csrf_from(client)
    with ThreadPoolExecutor(20) as pool:
        codes = list(pool.map(lambda _: client.post("/login", data={"password": "salah", "csrf_token": token},
                                                    follow_redirects=False).status_code, range(20)))
    assert codes.count(303) == auth.MAX_FAIL and codes.count(429) == 20 - auth.MAX_FAIL


def test_login_over_http_with_secure_cookie_explains(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "COOKIE_SECURE", True)
    lan = TestClient(client.app, base_url="http://192.168.10.5:8080")
    assert "COOKIE_SECURE=true" in lan.get("/login").text
    r = lan.post("/login", data={"password": PASSWORD, "csrf_token": "x"}, follow_redirects=False)
    assert r.status_code == 403 and "COOKIE_SECURE=true" in r.text
    https = TestClient(client.app, base_url="https://192.168.10.5")
    assert "COOKIE_SECURE" not in https.get("/login").text


def test_segment_seconds_must_be_60():
    from config import Settings
    with pytest.raises(ValidationError, match="GARDU_SEGMENT_SECONDS"):
        Settings(GARDU_SEGMENT_SECONDS=300)


def test_error_page_hides_menu_for_guests(client):
    csrf_from(client)                                  # sesi tamu sudah punya token CSRF
    r = client.get("/tidak-ada")
    assert r.status_code == 404 and "Keluar" not in r.text and 'href="/cameras"' not in r.text


def test_error_page_keeps_menu_when_logged_in(logged):
    r = logged.get("/tidak-ada", headers={"HX-Request": ""})
    assert r.status_code == 404 and "Keluar" in r.text


def test_wrong_db_key_gives_clear_error():
    from cryptography.fernet import Fernet
    other = Fernet(Fernet.generate_key()).encrypt(b"pw").decode()
    with db.connect() as conn:
        conn.execute("INSERT INTO cameras (id, name, ip, device_id, username, password_enc) "
                     "VALUES ('kunci-lama', 'Kunci lama', '192.168.99.99', 1, 'admin', ?)", (other,))
    try:
        with pytest.raises(RuntimeError, match="GARDU_DB_KEY tidak cocok.*kunci-lama"):
            db.check_key()
    finally:
        db.delete_camera("kunci-lama")
    db.check_key()
