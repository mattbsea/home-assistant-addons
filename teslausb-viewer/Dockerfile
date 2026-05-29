ARG BUILD_FROM
FROM ${BUILD_FROM}

# Pinned rclone version (official .deb gives /usr/bin/rclone)
ARG RCLONE_VERSION=v1.69.1

# System dependencies:
#   python3 venv  -> the FastAPI app
#   ffmpeg        -> reserved for the future HEVC->H.264 transcoding fallback (seam only in v1)
#   gosu          -> drop root to the unprivileged app user at startup
#   curl/ca-certs -> fetch the pinned rclone package
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        gosu \
        curl \
        ca-certificates \
    && curl -fsSL -o /tmp/rclone.deb \
        "https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/rclone.deb \
    && rm -f /tmp/rclone.deb \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user that owns /data and runs the web app
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
COPY scripts/ /opt/scripts/
RUN chmod +x /run.sh /opt/scripts/*.sh

WORKDIR /opt/teslausb-viewer
CMD ["/run.sh"]
