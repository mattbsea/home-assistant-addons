ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 24 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install OmniRoute globally
RUN npm install -g --no-audit --no-fund omniroute

# Create non-root user
RUN groupadd --gid 1000 omniroute \
    && useradd --uid 1000 --gid omniroute --shell /bin/bash --create-home omniroute

# Create data directory and set ownership
RUN mkdir -p /data/omniroute && chown -R omniroute:omniroute /data/omniroute

# Copy startup script
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Expose OmniRoute port
EXPOSE 20128

# Use exec form for proper signal handling
CMD ["/run.sh"]
