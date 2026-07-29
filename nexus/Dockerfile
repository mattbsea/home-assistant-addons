ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG NEXUS_VERSION=3.94.1-06
ARG NEXUS_SHA256=8d80b7c00217a1cdbf4bb39da72830a0bb2b93c7de3a27ade4695f6f7b6c0a59

ENV FT_ADDON_VERSION=${BUILD_VERSION}

ENV NEXUS_HOME=/opt/sonatype/nexus \
    SONATYPE_WORK=/opt/sonatype/sonatype-work

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gpg \
        sudo \
        unzip \
    && curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        | gpg --dearmor -o /usr/share/keyrings/adoptium.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb bookworm main" \
        > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        temurin-21-jre \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 200 -r nexus \
    && useradd --uid 200 -r nexus -g nexus -s /bin/false -d ${NEXUS_HOME} -c 'Nexus Repository Manager user'

RUN curl -fsSL \
        "https://download.sonatype.com/nexus/3/sonatype-nexus-repository-${NEXUS_VERSION}-assembly.zip" \
        -o /tmp/nexus.zip \
    && echo "${NEXUS_SHA256} /tmp/nexus.zip" | sha256sum -c - \
    && unzip /tmp/nexus.zip -d /opt/sonatype/ \
    && rm /tmp/nexus.zip \
    && mv "/opt/sonatype/nexus-${NEXUS_VERSION}" ${NEXUS_HOME} \
    && chown -R nexus:nexus /opt/sonatype \
    && chmod a+x ${NEXUS_HOME}/bin/nexus

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
