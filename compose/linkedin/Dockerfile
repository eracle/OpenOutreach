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

# Install Playwright Chromium to a shared path accessible by any user
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
ENV EDITOR=nano
RUN playwright install --with-deps chromium

# VNC stack (started conditionally via ENABLE_VNC=true at runtime)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb x11vnc python3-websockify curl nano \
    && curl -fsSL https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz \
        | tar -xz -C /opt \
    && mv /opt/noVNC-1.6.0 /opt/noVNC \
    && cp /opt/noVNC/vnc.html /opt/noVNC/index.html \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (slim image doesn't ship one)
RUN useradd -m -u 1000 ubuntu

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

COPY ./compose/linkedin/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

COPY --chown=ubuntu:ubuntu . ${APP_HOME}
RUN chown ubuntu:ubuntu ${APP_HOME}

USER ubuntu
WORKDIR ${APP_HOME}

CMD ["/start"]
