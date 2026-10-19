"""Uji kamera. Kamera online: periksa pipeline yang berjalan (tanpa koneksi baru, kamera V380 membatasi jumlah
koneksi). Kamera offline atau belum disimpan: login langsung seperti `--snapshot` di panduan."""
import asyncio
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from recorder import V380, CameraRecorder

DIRECT_TIMEOUT = 30
_slots = asyncio.Semaphore(2)       # batasi uji langsung bersamaan (tiap uji = satu subprocess + ffmpeg)

AUTH_HINTS = {
    "Invalid username": "username salah",
    "Invalid password": "password salah",
    "Invalid device id": "Device ID salah",
}


@dataclass
class Step:
    label: str
    status: str          # ok | warn | fail | skip
    detail: str = ""


def verdict(steps: list[Step]) -> str:
    fail = next((s for s in steps if s.status == "fail"), None)
    warn = next((s for s in steps if s.status == "warn"), None)
    if fail:
        return f"✘ Gagal di: {fail.label}"
    if warn:
        return f"⚠ {warn.label} perlu perhatian"
    return "✔ Semua langkah OK"


async def check_port(ip: str, port: int) -> Step:
    label = f"Port {port} terbuka"
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 3)
        w.close()
        return Step(label, "ok", f"nc -vz {ip} {port}")
    except (TimeoutError, OSError) as err:
        reason = "timeout" if isinstance(err, asyncio.TimeoutError) else err.strerror or str(err)
        return Step(label, "fail", f"{reason} — cek IP, kabel/WiFi, dan port kamera")


def _auth_error(stderr: str) -> str | None:
    m = re.search(r"Auth failed\. Code: (\d+) \(([^)]+)\)", stderr)
    if not m:
        return None
    hint = AUTH_HINTS.get(m.group(2))
    base = f"loginResult {m.group(1)}: {m.group(2)}"
    return f"{base} → {hint}; cocokkan dengan aplikasi V380 Pro" if hint else base


async def direct_login(ip: str, port: int, device_id: int, username: str, password: str, quality: int) -> list[Step]:
    login = "Login (loginResult 1001)"
    env = {"PATH": os.environ.get("PATH", ""), "V380_IP": ip, "V380_PORT": str(port),
           "V380_DEVICE_ID": str(device_id), "V380_USERNAME": username, "V380_PASSWORD": password,
           "V380_QUALITY": str(quality)}
    with tempfile.TemporaryDirectory(prefix="uji-") as tmp:
        out = Path(tmp) / "uji.jpg"
        async with _slots:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(V380), "--snapshot", str(out), env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _o, err = await asyncio.wait_for(proc.communicate(), DIRECT_TIMEOUT)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return [Step(login, "fail", f"tidak ada jawaban dalam {DIRECT_TIMEOUT} detik"),
                        Step("Data video mengalir", "skip")]
        text = err.decode(errors="replace")
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return [Step(login, "ok", "login langsung berhasil"),
                    Step("Data video mengalir", "ok", f"keyframe diterima ({out.stat().st_size // 1024} KB)")]
        auth = _auth_error(text)
        if auth:
            return [Step(login, "fail", auth), Step("Data video mengalir", "skip")]
        last = next((ln for ln in reversed(text.strip().splitlines()) if ln.strip()), "tanpa pesan")
        if "Capturing keyframe" not in text:   # belum lolos auth: bukan salah kredensial, bukan masalah video
            return [Step(login, "fail", f"{last[:200]} — yang menjawab di port ini mungkin bukan kamera V380"),
                    Step("Data video mengalir", "skip")]
        return [Step(login, "ok", "login diterima"), Step("Data video mengalir", "fail", last[:200])]


def _pipeline_steps(rec: CameraRecorder) -> list[Step]:
    cam = rec.cam
    steps = [Step("Login (loginResult 1001)", "ok", "memakai sesi pipeline yang berjalan")]
    ago = time.time() - rec.last_data
    steps.append(Step("Data video mengalir", "ok",
                      f"{rec.mbps:.1f} Mbps, data terakhir {ago:.0f} detik lalu".replace(".", ",")))
    steps.append(Step("FPS sesuai config", "skip", f"config {cam.fps} fps; cek dengan ffprobe pada rekaman"))
    seg = rec.latest_segment()
    if seg is None:
        steps.append(Step("Segmen terakhir tertulis", "fail", "belum ada file segmen"))
    else:
        age = time.time() - seg.stat().st_mtime
        steps.append(Step("Segmen terakhir tertulis", "ok" if age < 150 else "warn",
                          f"{seg.name} ({age:.0f} detik lalu)"))
    return steps


async def run_test(ip: str, port: int, device_id: int, username: str, password: str, quality: int,
                   rec: CameraRecorder | None) -> tuple[str, list[Step]]:
    """`rec` diisi hanya bila kamera tersimpan, sedang online, dan parameter koneksinya sama dengan form."""
    port_step = await check_port(ip, port)
    if port_step.status == "fail":
        skipped = [Step("Login (loginResult 1001)", "skip"), Step("Data video mengalir", "skip")]
        return "Kamera tidak terjangkau.", [port_step, *skipped]
    if rec is not None and rec.state == "online":
        return ("Kamera online: memeriksa pipeline yang berjalan (tanpa koneksi baru, kamera V380 "
                "membatasi jumlah koneksi).", [port_step, *_pipeline_steps(rec)])
    steps = await direct_login(ip, port, device_id, username, password, quality)
    return "Kamera offline atau belum disimpan: mencoba login langsung ke kamera.", [port_step, *steps]
