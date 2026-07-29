#!/usr/bin/with-contenv bashio
set +e +u +E +o pipefail

JAVA_MEM=$(bashio::config 'java_mem')
JFROG_HOME=/opt/jfrog/artifactory

mkdir -p /data/artifactory/var/etc /data/artifactory/var/data /data/artifactory/var/log
chown -R 185:185 /data/artifactory

if [ -L "${JFROG_HOME}/var" ] || [ ! -d "${JFROG_HOME}/var" ]; then
  rm -rf "${JFROG_HOME:?}/var"
fi

ln -sf /data/artifactory/var "${JFROG_HOME}/var"

cat > /data/artifactory/var/etc/system.yaml <<EOF
artifactory:
  port: 8091
router:
  entrypoints:
    externalPort: 8090
EOF

chown 185:185 /data/artifactory/var/etc/system.yaml

export JFROG_HOME
export ARTIFACTORY_JAVA_OPTIONS="${JAVA_MEM}"

bashio::log.info "Starting Artifactory Repository (JFROG_HOME=${JFROG_HOME})..."
bashio::log.info "JVM options: ${ARTIFACTORY_JAVA_OPTIONS}"

exec sudo -E -u artifactory "${JFROG_HOME}/app/bin/artifactory.sh"
