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

# Start PostgreSQL
mkdir -p /data/artifactory/postgres
chown -R postgres:postgres /data/artifactory/postgres
chmod 700 /data/artifactory/postgres

if [ ! -f /data/artifactory/postgres/PG_VERSION ]; then
  su - postgres -c "/usr/lib/postgresql/*/bin/initdb -D /data/artifactory/postgres --auth-local=trust --auth-host=md5"
  echo "host all all 0.0.0.0/0 md5" >> /data/artifactory/postgres/pg_hba.conf
  echo "listen_addresses = 'localhost'" >> /data/artifactory/postgres/postgresql.conf
  echo "port = 5432" >> /data/artifactory/postgres/postgresql.conf
fi

service postgresql start
# Create database and user if they don't exist
su - postgres -c "psql -c \"CREATE USER artifactory WITH PASSWORD 'artifactory';\" 2>/dev/null || true"
su - postgres -c "psql -c \"CREATE DATABASE access OWNER artifactory;\" 2>/dev/null || true"

cat > /data/artifactory/var/etc/system.yaml <<EOF
artifactory:
  port: 8091
  tomcat:
    maintenanceConnector:
      port: 8092
router:
  entrypoints:
    internalPort: 8046
    externalPort: 8090
security:
  join:
    access:
      autoConfigure: true
shared:
  node:
    id: "$(hostname)"
  database:
    host: "localhost"
    port: "5432"
    name: "access"
    user: "artifactory"
    password: "artifactory"
EOF

chown 185:185 /data/artifactory/var/etc/system.yaml

export JFROG_HOME
export ARTIFACTORY_JAVA_OPTIONS="${JAVA_MEM}"

bashio::log.info "Starting Artifactory Repository (JFROG_HOME=${JFROG_HOME})..."
bashio::log.info "JVM options: ${ARTIFACTORY_JAVA_OPTIONS}"

exec sudo -E -u artifactory "${JFROG_HOME}/app/bin/artifactory.sh"
