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

# Generate master key if not exists (required for Artifactory 7.x topology service)
MASTER_KEY_FILE="/data/artifactory/var/etc/security/master.key"
mkdir -p "$(dirname "$MASTER_KEY_FILE")"
if [ ! -f "$MASTER_KEY_FILE" ]; then
  bashio::log.info "Generating Artifactory master key..."
  openssl rand -hex 32 > "$MASTER_KEY_FILE"
  chmod 400 "$MASTER_KEY_FILE"
else
  bashio::log.info "Using existing Artifactory master key"
fi
chown -R 185:185 /data/artifactory/var/etc/security

# Start PostgreSQL
bashio::log.info "Starting PostgreSQL..."
mkdir -p /data/artifactory/postgres
if ! id postgres >/dev/null 2>&1; then
  groupadd --gid 999 -r postgres
  useradd --uid 999 -r postgres -g postgres -s /bin/false -d /var/lib/postgresql -c 'PostgreSQL user'
fi
chown -R postgres:postgres /data/artifactory/postgres
chmod 700 /data/artifactory/postgres

PG_BIN="/usr/lib/postgresql/*/bin"
PG_DATA="/data/artifactory/postgres"

if [ ! -f "${PG_DATA}/PG_VERSION" ]; then
  su - postgres -c "${PG_BIN}/initdb -D ${PG_DATA} --auth-local=trust --auth-host=md5 -U postgres"
  echo "host all all 0.0.0.0/0 md5" >> "${PG_DATA}/pg_hba.conf"
  echo "listen_addresses = 'localhost'" >> "${PG_DATA}/postgresql.conf"
fi

su - postgres -c "${PG_BIN}/pg_ctl -D ${PG_DATA} -l ${PG_DATA}/logfile start"
while ! su - postgres -c "${PG_BIN}/pg_isready -d postgres" >/dev/null 2>&1; do
  bashio::log.info "Waiting for PostgreSQL to start..."
  sleep 1
done
bashio::log.info "PostgreSQL is ready"

# Create database and user
su - postgres -c "psql -c \"CREATE USER artifactory WITH PASSWORD 'artifactory' SUPERUSER;\" 2>/dev/null || true"
su - postgres -c "psql -c \"CREATE DATABASE access OWNER artifactory;\"" 2>/dev/null || true

# Write system.yaml with database configuration
cat > /data/artifactory/var/etc/system.yaml <<EOF
artifactory:
  port: 8091
  database:
    type: postgresql
    driver: org.postgresql.Driver
    entity: access
    url: jdbc:postgresql://localhost:5432/access
    user: artifactory
    password: artifactory
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
      url: http://localhost:8040
shared:
  node:
    id: "$(hostname)"
EOF

chown 185:185 /data/artifactory/var/etc/system.yaml

export JFROG_HOME
export ARTIFACTORY_JAVA_OPTIONS="${JAVA_MEM}"
export ARTIFACTORY_SECURITY_MASTER_KEY_FILE="$MASTER_KEY_FILE"

bashio::log.info "Starting Artifactory Repository (JFROG_HOME=${JFROG_HOME})..."
bashio::log.info "JVM options: ${ARTIFACTORY_JAVA_OPTIONS}"

# Start Access service first (it needs to be running before Router health checks)
bashio::log.info "Starting Access service..."
if [ -x "${JFROG_HOME}/app/access/bin/access.sh" ]; then
  su - artifactory -c "${JFROG_HOME}/app/access/bin/access.sh start"
fi

# Start Artifactory (runs in foreground)
exec sudo -E -u artifactory "${JFROG_HOME}/app/bin/artifactory.sh"