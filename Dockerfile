FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Jakarta PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY gardu_recorder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Kode aplikasi tidak disalin: di-mount dari ./gardu_recorder ke /app (docker-compose.yml), jadi update kode cukup
# git pull + docker compose restart. Rebuild hanya bila requirements.txt atau Dockerfile berubah.
# Bukan root. UID/GID harus boleh menulis folder rekaman di NAS (mis. --build-arg GARDU_UID=1026 di Synology).
# /data dibuat milik user ini agar volume bernama baru ikut dimiliki user ini.
ARG GARDU_UID=1000 GARDU_GID=1000
RUN groupadd -g "$GARDU_GID" gardu && useradd -u "$GARDU_UID" -g gardu -M -s /usr/sbin/nologin gardu \
    && mkdir -p /data /recordings && chown gardu:gardu /data /recordings
USER gardu
# GARDU_DATA_DIR (/data) = volume SQLite + ekspor; folder rekaman = /recordings (mount NAS)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
