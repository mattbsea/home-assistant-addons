ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Node.js (for supergateway) and Python (for MCP server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    nodejs \
    npm \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install supergateway globally (HTTP/SSE bridge for stdio MCP servers)
RUN npm install -g supergateway

# Create Python venv and install MCP dependencies
RUN python3 -m venv /opt/npm-mcp/venv
COPY requirements.txt /opt/npm-mcp/requirements.txt
RUN /opt/npm-mcp/venv/bin/pip install --no-cache-dir -r /opt/npm-mcp/requirements.txt

# Copy MCP server
COPY npm_mcp_server.py /opt/npm-mcp/server.py

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
