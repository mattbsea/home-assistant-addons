#!/usr/bin/with-contenv bashio
set +e +u +E +o pipefail

JAVA_MEM=$(bashio::config 'java_mem')
JFROG_HOME=/opt/jfrog/artifactory

mkdir -p /data/artifactory/var/etc /data/artifactory/var/data /data/artifactory/var/log
chown -R 185:185 /data/artifactory

# Unconditional rm+relink: JFrog's tarball ships var/ as a real directory,
# so `ln -sf` (target already a real dir) nests the link inside as var/var
# instead of replacing var — leaving JFROG_HOME/var pointing at the stale
# bundled dir instead of our persistent /data/artifactory/var (orphaning
# the master key generated below).
rm -rf "${JFROG_HOME:?}/var"
ln -s /data/artifactory/var "${JFROG_HOME}/var"

# Clean up stale PID files and Derby database from previous container runs
find "${JFROG_HOME}/app" -name "*.pid" -delete 2>/dev/null || true
rm -rf /data/artifactory/var/data/jfbus/derby
rm -rf /data/artifactory/var/data/derby

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

# Point the filestore at /media/USBDisk (only on first boot -- never overwrite
# a binarystore.xml the user has since customized, e.g. via a UI/API change).
BINARYSTORE_FILE="/data/artifactory/var/etc/artifactory/binarystore.xml"
if [ ! -f "$BINARYSTORE_FILE" ]; then
  bashio::log.info "Configuring filestore at /media/USBDisk/artifactory-data..."
  mkdir -p /media/USBDisk/artifactory-data
  chown -R 185:185 /media/USBDisk/artifactory-data
  mkdir -p "$(dirname "$BINARYSTORE_FILE")"
  cat > "$BINARYSTORE_FILE" <<'EOF'
<config version="v1">
    <chain template="file-system"/>
    <provider id="file-system" type="file-system">
        <baseDataDir>/media/USBDisk/artifactory-data</baseDataDir>
    </provider>
</config>
EOF
  chown 185:185 "$BINARYSTORE_FILE"
fi

# Start PostgreSQL
bashio::log.info "Starting PostgreSQL..."
mkdir -p /data/artifactory/postgres
if ! id postgres >/dev/null 2>&1; then
  groupadd --gid 999 -r postgres
  useradd --uid 999 -r postgres -g postgres -s /bin/bash -d /var/lib/postgresql -c 'PostgreSQL user'
fi
chown -R postgres:postgres /data/artifactory/postgres
chmod 700 /data/artifactory/postgres

PG_BIN=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -1)
PG_DATA="/data/artifactory/postgres"
PG_LOG="${PG_DATA}/pg.log"

if [ -z "$PG_BIN" ]; then
  bashio::log.error "PostgreSQL binaries not found in /usr/lib/postgresql/*/bin"
  bashio::log.info "Available: $(ls /usr/lib/postgresql/ 2>/dev/null || echo 'none')"
  exit 1
fi

bashio::log.info "PostgreSQL binaries: ${PG_BIN}"

if [ ! -f "${PG_DATA}/PG_VERSION" ]; then
  bashio::log.info "Initializing PostgreSQL database..."
  su - postgres -c "${PG_BIN}/initdb -D ${PG_DATA} --auth-local=trust --auth-host=md5 -U postgres"
  echo "host all all 0.0.0.0/0 md5" >> "${PG_DATA}/pg_hba.conf"
  echo "listen_addresses = 'localhost'" >> "${PG_DATA}/postgresql.conf"
fi

su - postgres -c "${PG_BIN}/pg_ctl -D ${PG_DATA} -l ${PG_LOG} start"
sleep 3
for i in 1 2 3 4 5; do
  if ! su - postgres -c "${PG_BIN}/pg_isready -d postgres" >/dev/null 2>&1; then
    bashio::log.info "Waiting for PostgreSQL to start... (attempt ${i}/5)"
    sleep 2
  else
    bashio::log.info "PostgreSQL is ready"
    break
  fi
done
if ! su - postgres -c "${PG_BIN}/pg_isready -d postgres" >/dev/null 2>&1; then
  bashio::log.error "PostgreSQL failed to start. Log:"
  cat "${PG_LOG}" 2>/dev/null || echo "No log file"
  exit 1
fi

# Create database and user
su - postgres <<'PGEOF'
psql -c "CREATE USER artifactory WITH PASSWORD 'artifactory' SUPERUSER;" 2>/dev/null
psql -c "CREATE DATABASE access OWNER artifactory;" 2>/dev/null
PGEOF

# Write system.yaml with database configuration
MASTER_KEY=$(cat "$MASTER_KEY_FILE")
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
jfconnect:
  enabled: false
shared:
  database:
    type: postgresql
    driver: org.postgresql.Driver
    url: jdbc:postgresql://localhost:5432/access?sslmode=disable
    username: artifactory
    password: artifactory
  node:
    id: "$(hostname)"
  security:
    masterKey: ${MASTER_KEY}
EOF

chown 185:185 /data/artifactory/var/etc/system.yaml

export JFROG_HOME
export ARTIFACTORY_JAVA_OPTIONS="${JAVA_MEM}"
export ARTIFACTORY_SECURITY_MASTER_KEY_FILE="$MASTER_KEY_FILE"

bashio::log.info "Starting Artifactory Repository (JFROG_HOME=${JFROG_HOME})..."
bashio::log.info "JVM options: ${ARTIFACTORY_JAVA_OPTIONS}"

export JFROG_HOME
export ARTIFACTORY_JAVA_OPTIONS="${JAVA_MEM}"
export ARTIFACTORY_SECURITY_MASTER_KEY_FILE="$MASTER_KEY_FILE"
export JFROG_ENV_FILE=""

exec sudo -E -u artifactory "${JFROG_HOME}/app/bin/artifactory.sh"