# Home Assistant Add-on: Nexus Repository

Self-hosted Sonatype Nexus Repository — universal artifact repository for Maven, npm, PyPI, Docker,
and many other component formats. Use it as a proxy cache for public registries and as private hosted
storage for your CI/CD pipeline (GitHub Actions, etc.).

## First boot

Nexus takes **2–3 minutes** to start on the first run (Java initialization + embedded database setup).

1. Install the add-on and start it.
2. Watch the logs: `ha supervisor log -s nexus_repository --follow` (or use the Supervisor panel).
3. Once startup finishes, retrieve the auto-generated admin password:
   ```bash
   cat /data/nexus-data/admin.password
   ```
4. Log in as `admin` with that password.
5. The wizard will prompt you to set a new admin password.

## Access

### Web UI

| Method | URL |
|--------|-----|
| HA sidebar ingress | Click **Nexus** in the HA sidebar |
| Direct (same LAN) | `http://<ha-host>:8086/` |

### REST API

Used by CI/CD tools (GitHub Actions, etc.):
`http://<ha-host>:8086/service/rest/v1/`

See the [Nexus REST API docs](https://help.sonatype.com/en/rest-and-integration-api.html).

## Docker registry

Nexus can serve as a private Docker registry. The add-on exposes extra ports for Docker repository
connectors that you configure in the Nexus UI:

| Port | Purpose | Exposed |
|------|---------|---------|
| 8086 | Main UI/API + Docker hosted (push/pull) | Yes |
| 8087 | Docker proxy (cache Docker Hub) | Container-internal |
| 8088 | Docker group (combine hosted + proxy) | Container-internal |
| 8089 | Extra registry port | Container-internal |

### Setup

1. Go to **Settings → Repositories → Create repository → docker (hosted)**.
2. Set **HTTP connector port** to `8082`.
3. Enable the **Docker Bearer Token Realm** under **Security → Realms**.
4. From your CI/CD runner:
   ```bash
   docker login <ha-host>:8086 -u admin -p <password>
   docker build -t <ha-host>:8086/my-image:latest .
   docker push <ha-host>:8086/my-image:latest
   ```

For proxy repositories, create a **docker (proxy)** repository pointing at
`https://registry-1.docker.io` and assign a connector port (e.g. 8083).

## GitHub Actions setup

Add a `docker/login-action` step in your workflow:

```yaml
- name: Log in to Nexus Docker registry
  uses: docker/login-action@v3
  with:
    registry: ${{ vars.NEXUS_HOST }}:8086
    username: ${{ secrets.NEXUS_USERNAME }}
    password: ${{ secrets.NEXUS_PASSWORD }}
```

For Maven/npm/PyPI artifacts, configure the Nexus repository URL in your build tool (e.g.,
`pom.xml`, `.npmrc`, `pip.conf`) pointing at `http://<ha-host>:8086/repository/<repo-name>/`.

## Media mount

The `/media` directory from Home Assistant is mounted into the add-on. You can use it as a Nexus
blob store target:

1. In Nexus, go to **Settings → Repository → Blob Stores**.
2. Create a new **File** blob store.
3. Set **Path** to `/media/nexus-blobs`.

This is useful for large artifacts (Docker layers, Maven builds) on systems with separate media
storage.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `java_mem` | `-Xms2g -Xmx2g -XX:MaxDirectMemorySize=2g` | JVM heap and direct memory settings |

Adjust `java_mem` based on available RAM. Recommended values:

| Host RAM | `java_mem` |
|----------|-----------|
| 8 GB | `-Xms2g -Xmx2g -XX:MaxDirectMemorySize=2g` |
| 16 GB | `-Xms4g -Xmx4g -XX:MaxDirectMemorySize=4g` |
| 32 GB | `-Xms8g -Xmx8g -XX:MaxDirectMemorySize=8g` |

## Stopping

Nexus needs time to flush its databases on shutdown. Allow at least 60 seconds:
```bash
ha supervisor stop nexus_repository
```

## Known issues

- **armv7 is not supported** — Nexus requires a 64-bit JVM (amd64 or aarch64 only).
- **First boot is slow** (2–3 min) — the embedded database initializes. Subsequent starts are faster.
- **No automatic backup** — back up `/data/nexus-data/` periodically (shut down Nexus first).
