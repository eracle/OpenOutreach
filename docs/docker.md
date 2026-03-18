# Docker Installation and Usage

## Quick Start (Pre-built Image — Recommended)

Pre-built production images are published to GitHub Container Registry on every push to `master`.

```bash
docker run --pull always -it -p 5900:5900 -v openoutreach_db:/app ghcr.io/eracle/openoutreach:latest
```

The interactive onboarding will guide you through LinkedIn credentials, LLM API key, and campaign setup on first run. All data (CRM database, cookies, model blobs, embeddings) persists in the `openoutreach_db` Docker volume.

### Available Tags

| Tag | Description |
|:----|:------------|
| `latest` | Latest build from `master` |
| `sha-<commit>` | Pinned to a specific commit |
| `1.0.0` / `1.0` | Semantic version (when tagged) |

### VNC (Live Browser View)

The container includes a VNC server for watching the automation live. Connect any VNC client to `localhost:5900` (no password).

On Linux with `vinagre`:
```bash
vinagre vnc://127.0.0.1:5900
```

### Stopping & Restarting

```bash
# Find the container
docker ps

# Stop it
docker stop <container-id>

# Restart (data persists in the openoutreach_db volume)
docker run --pull always -it -p 5900:5900 -v openoutreach_db:/app ghcr.io/eracle/openoutreach:latest
```

---

## Build from Source (Docker Compose)

For development or customization, you can build the image locally. The compose file (`local.yml`)
mounts the entire project directory into the container for live code editing.

### Prerequisites

- [Make](https://www.gnu.org/software/make/)
- [Docker](https://www.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)

### Build & Run

```bash
git clone https://github.com/eracle/OpenOutreach.git
cd OpenOutreach

# Build and start
make up
```

This builds the Docker image from source with `BUILD_ENV=local` (includes test dependencies) and starts the daemon.

**Note:** The compose file uses `HOST_UID` / `HOST_GID` environment variables (defaulting to 1000)
for file ownership. If your host UID differs from 1000, set them explicitly:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) make up
```

### Useful Commands

| Command | Description |
|:--------|:------------|
| `make build` | Build the Docker image without starting |
| `make up` | Build and start the service |
| `make stop` | Stop the running containers |
| `make attach` | Follow application logs |
| `make up-view` | Start + open VNC viewer (Linux, requires `vinagre`) |
| `make view` | Open VNC viewer standalone (requires `vinagre`) |
| `make docker-test` | Run the test suite in Docker |

### VNC with Docker Compose

The VNC server is exposed on port 5900. Use `make up-view` to auto-open it, or connect manually to `localhost:5900` with any VNC client.

### Volume Mounts

The pre-built `docker run` command uses a named Docker volume (`openoutreach_db`) for data persistence. The compose setup (`local.yml`) mounts the entire repo `.:/app` for live code editing during development.
