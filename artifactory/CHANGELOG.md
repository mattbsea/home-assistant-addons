# Changelog

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
