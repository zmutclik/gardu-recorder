#!/usr/bin/env bash
# Jalankan aplikasi Gardu (dev) di http://localhost:8035
#   ./start.sh            aplikasi + MediaMTX untuk live view (bila GARDU_LIVE_ENABLED=true di .env)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT=8035
HOST="${GARDU_HOST:-127.0.0.1}"   # GARDU_HOST=0.0.0.0 ./start.sh bila perlu dibuka dari perangkat lain di LAN
MTX_VERSION=1.21.1
MTX_SHA256_AMD64=653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee

stop_port() {   # matikan server lama di port ini bila ada
  local pids
  pids=$(lsof -t -i TCP:"$1" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "Menghentikan server lama di port $1 (PID: $pids)"
    kill $pids; sleep 1; kill -9 $pids 2>/dev/null || true
  fi
}

# venv + dependensi (sekali)
if [ ! -x "$ROOT/.venv/bin/uvicorn" ]; then
  uv venv -q "$ROOT/.venv"
  uv pip install -q -p "$ROOT/.venv/bin/python" -r "$ROOT/gardu_recorder/requirements.txt"
fi
PY="$ROOT/.venv/bin/python"

# .env di root (sekali, bila belum ada): rahasia acak + password login acak yang dicetak satu kali
ENV="$ROOT/.env"
if [ ! -f "$ENV" ]; then
  PASS=$("$PY" -c "import secrets;print(secrets.token_urlsafe(9))")
  {
    echo "GARDU_PASSWORD=$PASS"
    echo "GARDU_SECRET=$("$PY" -c "import secrets;print(secrets.token_urlsafe(48))")"
    echo "GARDU_DB_KEY=$("$PY" -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")"
    echo "GARDU_DATA_DIR=$ROOT/data"
    echo "GARDU_STORAGE_PATH=$ROOT/recordings"
    echo "DEBUG=true"
    echo "COOKIE_SECURE=false"
  } > "$ENV"
  chmod 600 "$ENV"
  echo "Dibuat $ENV — password login: $PASS (hanya ditampilkan sekali)"
fi
mkdir -p "$ROOT/data" "$ROOT/recordings"

# Live view (sekali): aktifkan di .env dev + secret HLS bersama Gardu ↔ MediaMTX. Set GARDU_LIVE_ENABLED=false untuk mematikan.
if ! grep -q '^GARDU_LIVE_ENABLED=' "$ENV"; then
  {
    echo "GARDU_LIVE_ENABLED=true"
    echo "GARDU_MEDIAMTX_HLS_SECRET=$("$PY" -c "import secrets;print(secrets.token_urlsafe(24))")"
  } >> "$ENV"
  echo "Live view diaktifkan di $ENV"
fi

start_mediamtx() {
  local mtx="$ROOT/.tools/mediamtx" secret pids
  if [ ! -x "$mtx" ]; then
    [ "$(uname -sm)" = "Linux x86_64" ] || { echo "Taruh binary MediaMTX v$MTX_VERSION di $mtx"; return 1; }
    local tgz="$ROOT/.tools/mediamtx.tar.gz"
    mkdir -p "$ROOT/.tools"
    echo "Mengunduh MediaMTX v$MTX_VERSION…"
    curl -fsSL -o "$tgz" \
      "https://github.com/bluenviron/mediamtx/releases/download/v$MTX_VERSION/mediamtx_v${MTX_VERSION}_linux_amd64.tar.gz"
    echo "$MTX_SHA256_AMD64  $tgz" | sha256sum -c --quiet || { rm -f "$tgz"; echo "checksum MediaMTX salah"; return 1; }
    tar -xzf "$tgz" -C "$ROOT/.tools" mediamtx && rm -f "$tgz"
  fi
  # MediaMTX lama (mis. sisa run sebelumnya) dimatikan; port dipakai program lain → berhenti, jangan dibunuh
  pids=$(lsof -t -i TCP:8888 -i TCP:8554 -sTCP:LISTEN 2>/dev/null | sort -u || true)
  for pid in $pids; do
    if [ "$(ps -o comm= -p "$pid")" = "mediamtx" ]; then kill "$pid"; sleep 1
    else echo "Port 8888/8554 dipakai proses lain (PID $pid): $(ps -o args= -p "$pid")"; return 1; fi
  done
  secret=$(grep '^GARDU_MEDIAMTX_HLS_SECRET=' "$ENV" | cut -d= -f2- | tr -d "'\"")
  MTX_HLSCDNSECRET="$secret" MTX_AUTHINTERNALUSERS_0_PASS="$secret" "$mtx" "$ROOT/deploy/mediamtx.yml" &
  MTX_PID=$!
  trap 'kill $MTX_PID 2>/dev/null || true' EXIT
  echo "MediaMTX v$MTX_VERSION (PID $MTX_PID) di 127.0.0.1:8554 (RTSP) dan 127.0.0.1:8888 (HLS)"
}

if grep -q '^GARDU_LIVE_ENABLED=true' "$ENV"; then
  start_mediamtx
fi

stop_port "$PORT"
echo "Gardu di http://$HOST:$PORT"
cd "$ROOT/gardu_recorder"
"$ROOT/.venv/bin/uvicorn" main:app --host "$HOST" --port "$PORT" --reload     # bukan exec: trap mematikan MediaMTX
