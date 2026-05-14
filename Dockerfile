# ---- frontend build ----
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ---- runtime ----
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pip deps first for layer caching.
COPY pyproject.toml README.md /app/
COPY src /app/src
# Copy built frontend into the package's static dir before install so the
# wheel's forced-include picks it up.
COPY --from=frontend /app/dist /app/src/rtsp_recorder/static
RUN pip install --no-cache-dir .

ENV RTSP_RECORDER_DATA_DIR=/data \
    RTSP_RECORDER_HOST=0.0.0.0 \
    RTSP_RECORDER_PORT=8765

VOLUME ["/data"]
EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["rtsp-recorder"]
