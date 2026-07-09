# ── Stage 1: build dependencies ─────────────────────────────────
FROM python:3.12-slim-bookworm AS deps

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY ./requirements /requirements
ARG BUILD_ENV=production
RUN uv pip install --system -r /requirements/${BUILD_ENV}.txt

# ── Stage 2: runtime ────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# Copy installed Python packages from the build stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

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

COPY ./compose/linkedin/entrypoint /entrypoint
COPY ./compose/linkedin/start /start
RUN sed -i 's/\r$//g' /entrypoint /start && chmod +x /entrypoint /start

COPY --chown=ubuntu:ubuntu . ${APP_HOME}
RUN chown ubuntu:ubuntu ${APP_HOME}

ENTRYPOINT ["/entrypoint"]
CMD ["/start"]
