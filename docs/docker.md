# Docker — Running OpenOutreach on a Server

> **This is not the install path.** The supported install is `uvx openoutreach find 10` (or
> `pip install openoutreach`) — see the README quick start. This page is for running it unattended on
> a server, which is the one job the image still does better than a shell.
> Development and tests run natively; there is no `docker-test`.

> **There is no daemon any more.** The work verb is bounded by a goal, so **the container runs one job
> and exits** — `docker run … 10 emails` means ten more leads with addresses, then stop. Nothing here
> loops; whatever schedules the container (a systemd timer, a cron entry) decides how often, which is
> the right place for that decision. A container that ends is also a container you can see fail.

The image is a slim Python runtime with **no browser and no VNC** — a venv at `/opt/venv` holding the
installed package, and nothing from the build stage.

## Quick Start (Pre-built Image)

Pre-built images are published to GitHub Container Registry.

```bash
docker run --pull always -it -v ~/.openoutreach/data:/app/data \
  ghcr.io/eracle/openoutreach:latest 10 emails > leads.csv
```

- **The arguments are the goal.** Anything after the image name goes to `find`, so `10 emails` is ten
  more leads carrying an address; with none given it finds one. The CSV lands on **stdout** —
  redirect it, and note that it carries every lead in the store, so the newest file supersedes the last.
- `-it` is only needed for the **interactive onboarding** on first run — product/objective → LLM key →
  BetterContact key → your email → country → newsletter/legal. Configure those by environment instead
  and the container needs no TTY at all. Do not pass `-t` when redirecting: a TTY makes stdout and
  stderr the same stream, and the CSV would arrive with the logs mixed into it.
- `-v ~/.openoutreach/data:/app/data` persists everything (CRM database, model blobs, embeddings) on
  your host across restarts. The image sets `OPENOUTREACH_DB=/app/data/db.sqlite3`.

There are **no ports to publish** — there is no web server of its own and no browser to watch.
(To browse your CRM, run the Django Admin separately; see below.)

### Available Tags

| Tag | Description |
|:----|:------------|
| `latest` | Latest published build |
| `sha-<commit>` | Pinned to a specific commit |
| `1.0.0` / `1.0` | Semantic version (when tagged) |

### Running it repeatedly

The container exits when its goal is met, so "keep it running" is a scheduling question and belongs to
whatever schedules things on that host. A systemd timer firing hourly:

```ini
# openoutreach.service — pair with a .timer of your choosing
[Service]
Type=oneshot
ExecStart=/usr/bin/docker run --rm -v /srv/openoutreach:/app/data \
  ghcr.io/eracle/openoutreach:latest 10 emails
StandardOutput=append:/srv/openoutreach/leads.csv
```

Exit 0 means the goal was met; non-zero means it stopped short and said why on stderr, which the
journal will have. Stopping it is `docker stop`, and data persists in the mounted directory —
the number you ask for is *more than you already have*, so the next run continues rather than
restarting.

### Look at what it found

**There is no web surface.** The bundled binary is a CLI over two libraries and ships no URLconf and
no Django Admin — its answers are `openoutreach status`, the CSV that `openoutreach find 0` prints,
and the SQLite file itself:

```bash
docker run --rm -v /srv/openoutreach:/app/data \
  ghcr.io/eracle/openoutreach:latest openoutreach status
```

To browse the rows in a GUI, open `/app/data/db.sqlite3` with any SQLite client — one file holds the
finder's leads and the sender's mail log both.

---

## Build from Source (Docker Compose)

`local.yml` builds the same production image from a checkout. It is what the prod VM uses — one
directory per operator, each with its own CRM.

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

**The code is what the image was built from.** Only `./data` is mounted, so moving an instance
forward is `git pull` + rebuild — never a live edit inside the container.

**Note:** The compose file uses `HOST_UID` / `HOST_GID` environment variables (defaulting to 1000)
for file ownership. If your host UID differs from 1000, set them explicitly:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) make up
```

### Useful Commands

| Command | Description |
|:--------|:------------|
| `make build` | Build the Docker image without starting |
| `make up` | Build and run one job |
| `make stop` | Stop the running containers |
| `make logs` | Follow application logs |

### Use an existing `db.sqlite3`

To run against a database file you already have, bind-mount the host **directory** containing it onto
`/app/data` (the image opens `/app/data/db.sqlite3`):

```bash
docker run --pull always -v ~/.openoutreach/data:/app/data ghcr.io/eracle/openoutreach:latest
```

Place your `db.sqlite3` inside the mounted directory (`~/.openoutreach/data/` above; swap for your own
path). Two caveats: the dir and file must be writable by uid 1000 (the container user) or writes fail
with `readonly database`; and `find` applies migrations on the way in, so back the file up first
(`cp db.sqlite3{,.bak}`) if it's precious.

Note that a native install uses the **same default path**, so `uvx openoutreach` and the container can
be pointed at one CRM — but never at the same time. **One job per database, ever**: SQLite's WAL lets
`status` read alongside a running job, not two jobs write.
