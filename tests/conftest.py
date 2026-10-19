import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Env harus siap sebelum modul aplikasi diimpor (settings dibaca saat impor)
_tmp = Path(tempfile.mkdtemp(prefix="gardu-test-"))
PASSWORD = "rahasia-uji-123"            # ≥12 karakter: DEBUG tidak aktif di test
(_tmp / "rec").mkdir()
os.environ.update(
    GARDU_PASSWORD=PASSWORD,
    GARDU_SECRET="x" * 40,
    GARDU_DB_KEY=Fernet.generate_key().decode(),
    GARDU_STORAGE_PATH=str(_tmp / "rec"),
    GARDU_RETENTION_DAYS="14",
    GARDU_LIVE_ENABLED="false",
    GARDU_DATA_DIR=str(_tmp / "data"),
    COOKIE_SECURE="false",
    GARDU_CAMERA_NETWORKS='["192.168.0.0/16", "127.0.0.0/8"]',   # test memakai listener palsu di 127.0.0.1
)

import auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _tmp


@pytest.fixture
def client():
    auth._fails.clear()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def csrf_from(client: TestClient) -> str:
    page = client.get("/login")
    import re
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


@pytest.fixture
def logged(client):
    token = csrf_from(client)
    r = client.post("/login", data={"password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    page = client.get("/dashboard")
    import re
    token = re.search(r"X-CSRF-Token\": \"([^\"]+)", page.text).group(1)
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return client
