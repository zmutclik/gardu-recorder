import shutil

import config
import db
import httpx
import pytest

FORM = {"name": "Live Satu", "ip": "192.168.10.61", "port": "8800", "device_id": "1", "username": "admin",
        "password": "rahasia-kamera", "quality": "1", "fps": "16"}
SEEN: list[httpx.Request] = []


async def _body(data: bytes):
    yield data        # body streaming seperti server sungguhan (MockTransport membaca habis body bytes biasa)


def _upstream(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    if request.url.path == "/live-satu/index.m3u8":
        return httpx.Response(200, content=_body(b"#EXTM3U\n"), headers={
            "content-type": "application/vnd.apple.mpegurl", "set-cookie": "x=1", "server": "mediamtx"})
    return httpx.Response(404, content=_body(b"not found"))


def _clear(client):
    """Hapus kamera beserta folder rekamannya: tanpa itu kamera baru bernama sama mendapat ID berakhiran (-2)."""
    for cam in db.list_cameras():
        client.delete(f"/cameras/{cam.id}")
    for d in config.storage.path.glob("live-*"):
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cams(logged):
    _clear(logged)
    for i, name in enumerate(("Live Satu", "Live Dua", "Live Tiga", "Live Empat", "Live Lima")):
        assert logged.post("/cameras", data={**FORM, "name": name, "ip": f"192.168.10.{61 + i}"}).status_code == 200
    del logged.headers["HX-Request"]
    yield logged
    _clear(logged)


@pytest.fixture
def enabled(cams, monkeypatch):
    monkeypatch.setattr(config.live, "enabled", True)
    SEEN.clear()
    cams.app.state.live_http = httpx.AsyncClient(transport=httpx.MockTransport(_upstream), base_url="http://mtx")
    return cams


def test_requires_login(client):
    assert client.get("/live", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/live/live-satu/index.m3u8", follow_redirects=False).status_code == 303


def test_disabled_shows_notice_and_no_proxy(cams):
    r = cams.get("/live")
    assert "Live view nonaktif" in r.text and "hls.min.js" not in r.text
    assert cams.get("/live/live-satu/index.m3u8").status_code == 404


def test_grid_layouts(enabled):
    r = enabled.get("/live")
    assert r.text.count('data-live="/live/') == 5 and 'class="grid g9"' in r.text
    assert "hls.min.js" in r.text and "live.js" in r.text
    r = enabled.get("/live?n=4&hal=2")
    assert r.text.count('data-live="/live/') == 1 and "Halaman 2 dari 2" in r.text
    r = enabled.get("/live?n=1&cam=live-tiga")
    assert r.text.count('data-live="/live/') == 1 and 'data-live="/live/live-tiga/index.m3u8"' in r.text
    assert r.text.count('class="tile thumb') == 4 and "data-fullscreen" in r.text
    assert 'class="grid g9"' in enabled.get("/live?n=7").text          # tata letak tak dikenal → 9


def test_proxy_forwards_path_query_and_safe_headers(enabled):
    r = enabled.get("/live/live-satu/index.m3u8?_HLS_msn=3&_HLS_part=1")
    assert r.status_code == 200 and r.text == "#EXTM3U\n"
    assert r.headers["content-type"] == "application/vnd.apple.mpegurl"
    assert r.headers["cache-control"] == "no-store"
    assert "set-cookie" not in r.headers and r.headers.get("server") != "mediamtx"
    assert str(SEEN[-1].url) == "http://mtx/live-satu/index.m3u8?_HLS_msn=3&_HLS_part=1"
    assert "cookie" not in SEEN[-1].headers                # cookie sesi Gardu tidak diteruskan ke MediaMTX
    assert enabled.get("/live/live-satu/abc_seg9.mp4").status_code == 404


def test_proxy_upstream_down_is_502(enabled):
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    enabled.app.state.live_http = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://mtx")
    assert enabled.get("/live/live-satu/index.m3u8").status_code == 502


@pytest.mark.parametrize("path", ["/live/tidak-ada/index.m3u8", "/live/live-satu/.index.m3u8",
                                  "/live/live-satu/..%2F..%2Fx", "/live/LIVE/index.m3u8"])
def test_proxy_rejects_unknown_camera_and_bad_names(enabled, path):
    assert enabled.get(path).status_code in (404, 422)
    assert not SEEN
