# Home Assistant Add-on: Artifactory Repository

Self-hosted JFrog Artifactory OSS — universal artifact repository manager for Maven, npm, PyPI,
Docker, Conan, Helm, and many other package formats. Use it as a proxy cache for public registries
and as private hosted storage for your CI/CD pipeline.

## First boot

Artifactory takes **3–5 minutes** to start on the first run (Java initialization + embedded
database setup).

1. Install the add-on and start it.
2. Watch the logs for startup progress.
3. Once startup finishes (you'll see a message like "Artifactory is running"), navigate to the
   web UI.
4. Log in with the default credentials:
   - **Username:** `admin`
   - **Password:** `password`
   (You will be prompted to change the password on first login.)

## Access

### Web UI

| Method | URL |
|--------|-----|
| HA sidebar ingress | Click **Artifactory** in the HA sidebar |
| Direct (same LAN) | `http://<ha-host>:8090/` |

### REST API

Used by CI/CD tools (GitHub Actions, etc.):
`http://<ha-host>:8090/artifactory/api/`

All API requests are proxied through the Artifactory Router on port 8090, which
internally routes to the Artifactory service on port 8091.

For direct API access (bypassing the Router), map port 8091 as a direct port
and use `http://<ha-host>:8091/artifactory/api/`.

See the [JFrog REST API docs](https://jfrog.com/help/r/jfrog-rest-apis).

## Docker registry

Artifactory can serve as a private Docker registry via a Docker repository.

### Setup

1. Log in to the Artifactory web UI as `admin`.
2. Go to **Administration → Repositories → Repositories → Create a Repository**.
3. Select **Docker** as the package type.
4. Choose **Local**.
5. Set **Repository Key** (e.g., `docker-local`).
6. Under **Docker Settings**, set **HTTP port** to `8092` (use a dedicated port for each Docker repository).
7. From your CI/CD runner:
   ```bash
   docker login <ha-host>:8092 -u admin -p <password>
   docker build -t <ha-host>:8092/my-image:latest .
   docker push <ha-host>:8092/my-image:latest
   ```

For a proxy registry, create a **Remote** Docker repository pointing at
`https://registry-1.docker.io` and assign its own connector port (e.g. `8083`,
which you can add as an extra port in the add-on configuration).

## GitHub Actions setup

```yaml
- name: Publish to Artifactory
  env:
    ARTIFACTORY_URL: http://${{ vars.HA_HOST }}:8090
    ARTIFACTORY_USER: ${{ secrets.ARTIFACTORY_USERNAME }}
    ARTIFACTORY_PASS: ${{ secrets.ARTIFACTORY_PASSWORD }}
  run: |
    # Maven
    mvn deploy -DaltDeploymentRepository=snapshots::default::${ARTIFACTORY_URL}/artifactory/libs-snapshot-local

    # npm
    npm publish --registry ${ARTIFACTORY_URL}/artifactory/api/npm/npm-local/
```

For Docker, add a `docker/login-action` step:

```yaml
- name: Log in to Artifactory Docker registry
  uses: docker/login-action@v3
  with:
    registry: ${{ vars.HA_HOST }}:8092
    username: ${{ secrets.ARTIFACTORY_USERNAME }}
    password: ${{ secrets.ARTIFACTORY_PASSWORD }}
```

## Media mount

The `/media` directory from Home Assistant is mounted into the add-on. You can use it as an
Artifactory binarystore/filestore target for large artifacts.

To configure:
1. In Artifactory, go to **Administration → Artifactory → Filestore**.
2. Change the filestore type to **File System**.
3. Set the directory to `/media/artifactory-data`.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `java_mem` | `-Xms2g -Xmx2g` | JVM heap settings |

Adjust `java_mem` based on available RAM. Recommended values:

| Host RAM | `java_mem` |
|----------|-----------|
| 8 GB | `-Xms2g -Xmx2g` |
| 16 GB | `-Xms4g -Xmx4g` |
| 32 GB | `-Xms8g -Xmx8g` |

## Stopping

Artifactory needs time to flush its databases on shutdown. Allow at least 60 seconds:
```bash
ha supervisor stop artifactory_repository
```

## Known issues

- **armv7 is not supported** — Artifactory requires a 64-bit JVM (amd64 or aarch64 only).
- **First boot is slow** (3–5 min) — the embedded database initializes. Subsequent starts are
  faster but still need ~1–2 min for Java startup.
- **No automatic backup** — back up `/data/artifactory/` periodically (stop Artifactory first).
- **Default credentials** — change the `admin` password immediately after first login.
- **Large download** — the initial build downloads a ~1.7 GB tarball (includes embedded JDK).
