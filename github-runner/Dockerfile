ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG RUNNER_VERSION=2.335.1
ARG RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        jq \
        git \
        docker.io \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Download and stage the official GitHub Actions runner release, checksum-verified against the
# value GitHub publishes on the release page. Extracted once here into /opt/actions-runner;
# register-target.sh copies this into each target's own persistent work directory, since
# config.sh writes target-specific registration state into the directory it's run from.
RUN mkdir -p /opt/actions-runner \
    && cd /opt/actions-runner \
    && curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c - \
    && tar xzf runner.tar.gz \
    && rm runner.tar.gz \
    && ./bin/installdependencies.sh

COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
COPY status_server/ /opt/status_server/
RUN chmod +x /run.sh /opt/scripts/*.sh

CMD ["/run.sh"]
