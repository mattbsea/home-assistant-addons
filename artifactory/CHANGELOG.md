# Changelog

## 1.0.15

- Remove `security.join` section from system.yaml (single-node OSS doesn't need cluster/join config; was causing Access service ping retries)

## 1.0.14

- Use `shared.database` (instead of `artifactory.database`) so all services (jfbus, event, metadata) use PostgreSQL
- Remove stale Derby databases from persistent `/data` volume on startup to force PostgreSQL switch
- Use `username`/`password` keys (correct schema for `shared.database` section)

## 1.0.13

- Added `procps` package (provides `ps` command required by Artifactory startup scripts)
- Clean stale PID files from previous container runs before starting services

## 1.0.12

- Start Access service before Artifactory so master key is initialized first
- Wait loop until Access responds on port 8040 before starting Artifactory/Router

## 1.0.11

- Fixed PostgreSQL binary discovery: use `ls -d` to expand glob path
- Fixed postgres user shell: changed from `/bin/false` to `/bin/bash` for `su` compatibility
- Embedded master key directly in system.yaml
- Added PostgreSQL startup error handling with retry loop

## 1.0.10

- Added master key generation (`openssl rand -hex 32`) for Artifactory 7.x topology service
- Export `ARTIFACTORY_SECURITY_MASTER_KEY_FILE` environment variable

## 1.0.9

- Upgraded DB user to `SUPERUSER` for Access service schema creation
- Added PostgreSQL startup logging and status checks

## 1.0.8

- Fixed PostgreSQL container startup: use `pg_ctl` instead of `service` (no systemd)
- Create postgres user if it doesn't exist at runtime

## 1.0.7

- Added PostgreSQL support for Artifactory OSS (derby database is not allowed in 7.x)
- Updated system.yaml to configure PostgreSQL database connection parameters
- Added PostgreSQL installation to Dockerfile
- Added PostgreSQL startup script to run.sh

## 1.0.3

- Fixed startup crash: removed `run` argument from `artifactory.sh` (script failed to match the action in the OSS version; no-argument default runs in foreground)

## 1.0.2

- Fixed image build failure: added `mkdir -p /opt/jfrog` before tarball extraction

## 1.0.1

- Fixed startup command: `artifactory.sh foreground` → `artifactory.sh run`
- Fixed system.yaml port configuration:
  - Removed invalid `config:` wrapper key
  - Replaced invalid `shared.node.port` with `router.entrypoints.externalPort`
  - Replaced invalid `shared.dataDir` with correct `artifactory.port` + `router.entrypoints.externalPort`
  - Router listens on 8090 (ingress), Artifactory service on 8091 (direct API access)
- Updated port descriptions for clarity
- Artifactory 7.161.15 bundles OpenJDK 21.0.8 (no separate JDK install needed)

## 1.0.0

- Initial release
- JFrog Artifactory OSS 7.161.15
- Ingress web UI on port 8090
- Direct port access for CI/CD artifact upload (8091 Docker registry, 8092 extra)
- Persistent data at /data/artifactory
- Media mount at /media for filestore use
- Configurable JVM heap via java_mem option
