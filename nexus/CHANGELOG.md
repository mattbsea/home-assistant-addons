# Changelog

## 1.0.0

- Initial release
- Sonatype Nexus Repository 3.94.1-06
- Ingress web UI on port 8081
- Direct port access for CI/CD artifact upload (8081 API, 8082 Docker hosted)
- Persistent data at /data/nexus-data
- Media mount at /media for blob store use
- Configurable JVM heap via java_mem option
