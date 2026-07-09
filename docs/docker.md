# Docker Installation and Usage

OpenOutreach runs as a single browserless daemon — a slim Python image with **no browser and no VNC**. All you interact with is the terminal (for onboarding) and, optionally, the Django Admin.

## Quick Start (Pre-built Image — Recommended)

Pre-built images are published to GitHub Container Registry.

```bash
docker run --pull always -it -v ~/.openoutreach/data:/app/data ghcr.io/eracle/openoutreach:latest
```

- `-it` is required so the **interactive onboarding** can prompt you on first run — product/objective → LLM key → mailbox (paste an app password) → BetterContact key → your email → country → newsletter/legal.
- `-v ~/.openoutreach/data:/app/data` persists everything (CRM database, model blobs, embeddings) on your host across restarts.

There are **no ports to publish** — the daemon has no web server of its own and no browser to watch. (To browse your CRM, run the Django Admin separately; see below.)

### Available Tags

| Tag | Description |
|:----|:------------|
| `latest` | Latest published build |
| `sha-<commit>` | Pinned to a specific commit |
| `1.0.0` / `1.0` | Semantic version (when tagged) |

### Stopping & Restarting

```bash
# Find the container
docker ps

# Stop it
docker stop <container-id>

# Restart (data persists in the mounted directory)
docker run --pull always -it -v ~/.openoutreach/data:/app/data ghcr.io/eracle/openoutreach:latest
```

### View your CRM (Django Admin)

The daemon image runs the worker, not a web server. To browse Leads and Deals, run the admin server (locally or in a second container) and publish port 8000:

```bash
docker run --pull always -it -p 8000:8000 -v ~/.openoutreach/data:/app/data \
  ghcr.io/eracle/openoutreach:latest python manage.py runserver 0.0.0.0:8000
```

Then open **http://localhost:8000/admin/** (create a superuser first with `python manage.py createsuperuser`).

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
| `make up` | Build and start the daemon |
| `make stop` | Stop the running containers |
| `make logs` | Follow application logs |
| `make docker-test` | Run the test suite in Docker |

### Volume Mounts

The pre-built `docker run` command mounts a host directory at `/app/data` for persistence (database, config). The compose setup (`local.yml`) mounts the entire repo `.:/app` for live code editing during development.

### Use an existing `db.sqlite3`

To run against a database file you already have, bind-mount the host **directory** containing it onto `/app/data` (the app opens `/app/data/db.sqlite3`):

```bash
docker run --pull always -it -v ~/.openoutreach/data:/app/data ghcr.io/eracle/openoutreach:latest
```

Place your `db.sqlite3` inside the mounted directory (`~/.openoutreach/data/` above; swap for your own path). Two caveats: the dir and file must be writable by uid 1000 (the container user) or writes fail with `readonly database`; and `rundaemon` runs `migrate` on startup, so back the file up first (`cp db.sqlite3{,.bak}`) if it's precious.
