import asyncio
import shutil
import subprocess
import sys
import textwrap
import time

import pytest
from config import LiveConfig, StorageConfig
from db import Camera
from recorder import CameraRecorder, RecorderManager

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or "libx265" not in subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout,
    reason="butuh ffmpeg dengan libx265")

CAM = Camera(id="fake", name="Fake", ip="127.0.0.1", port=8800, device_id=1, username="u", quality=1, fps=10,
             password="p")


def fake_source(tmp_path, mode="stream"):
    """Pengganti v380_stream.py: H.265 Annex-B di stdout (mode 'stall': diam setelah 1 detik)."""
    script = tmp_path / "fake_v380.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
            "testsrc=size=320x240:rate=10", "-c:v", "libx265", "-x265-params", "log-level=none:keyint=10",
            "-f", "hevc", "-"], stdout=subprocess.PIPE)
        start = time.time()
        while True:
            data = p.stdout.read1(65536)
            if not data:
                break
            if "{mode}" == "stall" and time.time() - start > 1:
                time.sleep(60)
            sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
    """))
    return [sys.executable, str(script)]


async def wait_for(cond, timeout=20):
    for _ in range(int(timeout * 10)):
        if cond():
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="butuh ffprobe")
def test_pipeline_writes_playable_segments_and_stops_cleanly(tmp_path, monkeypatch):
    async def go():
        storage = StorageConfig(path=tmp_path, segment_seconds=10)
        mgr = RecorderManager(storage, LiveConfig())
        monkeypatch.setattr(CameraRecorder, "source_cmd", fake_source(tmp_path))
        mgr.start(CAM)
        rec = mgr.recs["fake"]
        assert rec.state == "connecting"
        assert await wait_for(lambda: rec.online and rec.bytes_total > 20000)
        assert rec.state == "online"
        await asyncio.sleep(2)
        await mgr.stop("fake")          # menutup ffmpeg dengan rapi → segmen bisa dibaca
        return rec

    rec = asyncio.run(go())
    files = sorted((tmp_path / "fake").glob("*/*.mp4"))
    assert files, "tidak ada segmen"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name", "-of", "csv=p=0", str(files[0])], capture_output=True, text=True)
    assert probe.stdout.strip() == "hevc", probe.stderr
    assert rec.latest_segment() == files[-1]


def test_stalled_source_is_restarted(tmp_path, monkeypatch):
    import recorder
    monkeypatch.setattr(recorder, "STALL_SECONDS", 3)

    async def go():
        mgr = RecorderManager(StorageConfig(path=tmp_path, segment_seconds=10), LiveConfig())
        monkeypatch.setattr(CameraRecorder, "source_cmd", fake_source(tmp_path, "stall"))
        mgr.start(CAM)
        rec = mgr.recs["fake"]
        ok = await wait_for(lambda: rec.restarts >= 1, timeout=15)
        await mgr.stop_all()
        return ok

    assert asyncio.run(go()), "watchdog tidak menyalakan ulang pipeline yang macet"


def test_stuck_recorder_ffmpeg_is_restarted(tmp_path, monkeypatch):
    """ffmpeg perekam yang tidak membaca stdin (mis. NAS hang) tidak boleh menahan pipeline selamanya."""
    import recorder
    monkeypatch.setattr(recorder, "STALL_SECONDS", 3)
    monkeypatch.setattr(CameraRecorder, "_rec_cmd", lambda self: [sys.executable, "-c", "import time; time.sleep(60)"])

    async def go():
        mgr = RecorderManager(StorageConfig(path=tmp_path, segment_seconds=10), LiveConfig())
        monkeypatch.setattr(CameraRecorder, "source_cmd", fake_source(tmp_path))
        mgr.start(CAM)
        rec = mgr.recs["fake"]
        ok = await wait_for(lambda: rec.restarts >= 1, timeout=60)   # pipe penuh ±20 dtk + tutup 10 dtk
        await mgr.stop_all()
        return ok

    assert asyncio.run(go()), "pipeline dengan ffmpeg perekam macet tidak dinyalakan ulang"


def test_dead_live_publisher_is_respawned_without_touching_recording(tmp_path, monkeypatch):
    """MediaMTX mati/restart: ffmpeg live keluar → dicoba lagi tiap LIVE_RETRY detik; pipeline rekam tetap jalan."""
    import recorder
    monkeypatch.setattr(recorder, "LIVE_RETRY", 1)
    spawned = []
    # pengganti ffmpeg live: terima 4 KB lalu keluar dengan error, seperti publish yang ditolak
    dies = [sys.executable, "-c", "import sys; sys.stdin.buffer.read(4096); sys.exit(1)"]

    def live_cmd(self):
        spawned.append(time.time())
        return dies

    monkeypatch.setattr(CameraRecorder, "_live_cmd", live_cmd)

    async def go():
        mgr = RecorderManager(StorageConfig(path=tmp_path, segment_seconds=10), LiveConfig(enabled=True))
        monkeypatch.setattr(CameraRecorder, "source_cmd", fake_source(tmp_path))
        mgr.start(CAM)
        rec = mgr.recs["fake"]
        ok = await wait_for(lambda: len(spawned) >= 3, timeout=20)
        online, restarts = rec.online, rec.restarts
        await mgr.stop_all()
        return ok, online, restarts

    ok, online, restarts = asyncio.run(go())
    assert ok, f"ffmpeg live tidak dicoba ulang (dijalankan {len(spawned)}×)"
    assert online and restarts == 0
    assert all(b - a >= 0.9 for a, b in zip(spawned, spawned[1:], strict=False)), "jeda coba ulang tidak dipatuhi"


def test_live_command_sends_real_timestamps(tmp_path):
    """Argumen _live_cmd yang sebenarnya, dengan keluaran RTSP diganti framecrc: PTS harus naik, bukan kosong/0."""
    raw = tmp_path / "raw.hevc"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "2",
                    "-c:v", "libx265", "-x265-params", "log-level=none:keyint=10", "-f", "hevc", str(raw)], check=True)
    cmd = CameraRecorder(CAM, StorageConfig(path=tmp_path), LiveConfig(enabled=True))._live_cmd()
    out = cmd.index("rtsp") - 1                        # "-f", "rtsp", "-rtsp_transport", "tcp", url
    cmd = [*cmd[:out], "-f", "framecrc", "-"]
    cmd[cmd.index("pipe:0")] = str(raw)
    lines = [ln for ln in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
             if ln and not ln.startswith("#")]
    pts = [int(ln.split(",")[2]) for ln in lines]
    assert len(pts) >= 10 and pts == sorted(pts) and pts[-1] > 0, pts[:5]


def test_live_publish_url_carries_credentials(tmp_path):
    live = LiveConfig(enabled=True, mediamtx_url="rtsp://127.0.0.1:8554", publish_password="a@b:c/d")
    url = CameraRecorder(CAM, StorageConfig(path=tmp_path), live)._live_url()
    assert url == "rtsp://gardu:a%40b%3Ac%2Fd@127.0.0.1:8554/fake"
    assert CameraRecorder(CAM, StorageConfig(path=tmp_path), LiveConfig())._live_url() == "rtsp://127.0.0.1:8554/fake"
