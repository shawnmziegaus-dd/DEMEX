# DEMEX server image: the bridge (Python stdlib HTTP server) + the CLI tools it shells out to.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pillow
COPY . .
# Runtime data (SQLite db, downloaded plan PDFs, rate card, ITB drops) lives on the mounted volume.
ENV DEMEX_BIND=0.0.0.0 DEMEX_BRIDGE_PORT=8080 DEMEX_DATA_DIR=/data
EXPOSE 8080
CMD ["python", "server/entry.py"]
