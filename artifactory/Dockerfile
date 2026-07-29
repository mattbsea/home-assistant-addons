ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG ARTIFACTORY_VERSION=7.161.15
ARG ARTIFACTORY_SHA256=8cb116d51e6815316aeebf019c5c3fc1ab359161e6cc9f75a4b3d40bd2c01df6

ENV FT_ADDON_VERSION=${BUILD_VERSION}
ENV JFROG_HOME=/opt/jfrog/artifactory

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        sudo \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 185 -r artifactory \
    && useradd --uid 185 -r artifactory -g artifactory -s /bin/false -d ${JFROG_HOME} -c 'JFrog Artifactory user'

RUN mkdir -p /tmp/artifactory \
    && curl -fsSL \
        "https://releases.jfrog.io/artifactory/bintray-artifactory/org/artifactory/oss/jfrog-artifactory-oss/${ARTIFACTORY_VERSION}/jfrog-artifactory-oss-${ARTIFACTORY_VERSION}-linux.tar.gz" \
        -o /tmp/artifactory/artifactory.tar.gz \
    && echo "${ARTIFACTORY_SHA256} /tmp/artifactory/artifactory.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/artifactory/artifactory.tar.gz -C /opt/jfrog/ \
    && rm -rf /tmp/artifactory \
    && mv "/opt/jfrog/artifactory-oss-${ARTIFACTORY_VERSION}" ${JFROG_HOME} \
    && chown -R artifactory:artifactory /opt/jfrog \
    && chmod a+x ${JFROG_HOME}/app/bin/artifactory.sh

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
