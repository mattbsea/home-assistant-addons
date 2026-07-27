#!/usr/bin/with-contenv bashio
set +e +u +E +o pipefail

JAVA_MEM=$(bashio::config 'java_mem')

mkdir -p /data/nexus-data /data/nexus-data/javaprefs
chown -R 200:200 /data/nexus-data

chmod 755 /media 2>/dev/null || true

export NEXUS_DATA=/data/nexus-data
export INSTALL4J_ADD_VM_PARAMS="${JAVA_MEM} -Djava.util.prefs.userRoot=/data/nexus-data/javaprefs"

bashio::log.info "Starting Nexus Repository (NEXUS_DATA=${NEXUS_DATA})..."
bashio::log.info "JVM options: ${INSTALL4J_ADD_VM_PARAMS}"

exec gosu nexus /opt/sonatype/nexus/bin/nexus run
