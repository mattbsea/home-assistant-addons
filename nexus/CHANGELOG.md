# Changelog

## 1.0.5

- Fix crash on startup: Nexus 3.94.1 requires Java 21 — upgraded from `openjdk-17-jre-headless` to `openjdk-21-jre-headless`

## 1.0.4

- Change default ports from 8081-8084 to 8086-8089 to avoid conflict with fleet-telemetry
- Add `null` mapping for Docker proxy/group/extra ports (configure connectors in Nexus UI)

## 1.0.3

- Fix build failure: Nexus 3.94.1 doesn't ship nexus.vmoptions — removed unnecessary `sed` line
- JVM settings are controlled entirely via INSTALL4J_ADD_VM_PARAMS at runtime

## 1.0.2

- Fix build failure: add default ARG values so build works without HA builder passing build.yaml args
- Add `InvalidDefaultArgInFrom` hadolint exception

## 1.0.1

- Fix build failure: replaced `gosu` with `sudo` (gosu is not a Debian package)
- Use `sudo -E -u nexus` for privilege drop (preserves env vars)

## 1.0.0

- Initial release
- Sonatype Nexus Repository 3.94.1-06
- Ingress web UI on port 8081
- Direct port access for CI/CD artifact upload (8081 API, 8082 Docker hosted)
- Persistent data at /data/nexus-data
- Media mount at /media for blob store use
- Configurable JVM heap via java_mem option
