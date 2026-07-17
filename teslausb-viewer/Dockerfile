ARG BUILD_FROM
FROM ${BUILD_FROM}

# System dependencies:
#   python3 venv  -> the FastAPI app
#   ffmpeg        -> poster-frame thumbnail generation (thumbnailer.py) and the reserved
#                    future HEVC->H.264 transcoding fallback
#   gosu          -> drop root to the unprivileged app user at startup
#   curl/ca-certs -> required by bashio at runtime (bashio talks to the Supervisor API over curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        gosu \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user that owns /data and the bind-mounted TeslaCam directory, and runs the web app
RUN groupadd --gid 1000 viewer \
    && useradd --uid 1000 --gid viewer --shell /bin/bash --create-home viewer

# Python dependencies managed with uv (https://github.com/astral-sh/uv)
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv
RUN uv venv /opt/teslausb-viewer/venv
COPY requirements.txt /opt/teslausb-viewer/requirements.txt
RUN VIRTUAL_ENV=/opt/teslausb-viewer/venv \
    uv pip install --no-cache -r /opt/teslausb-viewer/requirements.txt

# Application code
COPY app/ /opt/teslausb-viewer/app/

COPY run.sh /run.sh
RUN chmod +x /run.sh

WORKDIR /opt/teslausb-viewer
CMD ["/run.sh"]
