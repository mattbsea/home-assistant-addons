ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG NEXUS_VERSION
ARG NEXUS_SHA256

ENV FT_ADDON_VERSION=${BUILD_VERSION}

ENV NEXUS_HOME=/opt/sonatype/nexus \
    SONATYPE_WORK=/opt/sonatype/sonatype-work

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        ca-certificates \
        curl \
        unzip \
        gosu \
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
    && sed -i '/^-Xms/d;/^-Xmx/d;/^-XX:MaxDirectMemorySize/d' ${NEXUS_HOME}/bin/nexus.vmoptions \
    && chmod a+x ${NEXUS_HOME}/bin/nexus

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
