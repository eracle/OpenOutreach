# Production image. The supported install is `uv tool install openoutreach` — this exists for
# running a bounded job on a server from a timer (docs/infrastructure.md §7), not for local
# development and not for tests. Run those natively.

# ── Stage 1: build a venv holding the package and its dependencies ──
FROM python:3.12-slim-bookworm AS deps

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY . /src
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache /src

# ── Stage 2: runtime ────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# One self-contained directory: the package, its dependencies, and the
# `openoutreach` console script. Nothing from the build stage — no git, no uv.
COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

ENV EDITOR=nano

# The email-first daemon is browserless — no Playwright, no Xvfb, no VNC.
# gosu drops root → ubuntu in the entrypoint; nano backs $EDITOR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu nano \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (slim image doesn't ship one)
RUN useradd -m -u 1000 ubuntu

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

COPY ./compose/openoutreach/entrypoint /entrypoint
COPY ./compose/openoutreach/start /start
RUN sed -i 's/\r$//g' /entrypoint /start && chmod +x /entrypoint /start

# The package lives in the venv, so the CRM cannot default to a path beside the
# code. Name it explicitly: /app/data is the volume operators already mount.
ENV OPENOUTREACH_DB=/app/data/db.sqlite3

ENTRYPOINT ["/entrypoint"]
CMD ["/start"]
