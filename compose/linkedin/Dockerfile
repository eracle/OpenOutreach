# Use the official Playwright Python Docker image as base (pre-includes browsers and deps)
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

# Install Xvfb and x11vnc for virtual display and VNC access
RUN apt-get update && apt-get install -y xvfb x11vnc && rm -rf /var/lib/apt/lists/*

# Define the application directory
ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Define a build argument to determine which environment to use
ARG BUILD_ENV=production

# Install uv using pip (since the base has pip)
RUN pip install uv

# Copy requirements folder and install the dependencies based on BUILD_ENV using uv
# DjangoCRM is installed with --no-deps first to skip mysqlclient (we use SQLite)
COPY ./requirements /requirements
RUN uv pip install --system --no-deps -r /requirements/crm.txt \
    && uv pip install --system -r /requirements/${BUILD_ENV}.txt

RUN playwright install --with-deps chromium

# Copy necessary startup scripts
COPY ./compose/linkedin/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

# Copy the entire application code to the app directory (owned by non-root user)
COPY --chown=ubuntu:ubuntu . ${APP_HOME}

# Run as non-root user to avoid creating root-owned files on bind mounts
# Base image already has 'ubuntu' user with UID 1000
USER ubuntu

# Ensure assets directories exist (runs as ubuntu since COPY --chown made /app writable)
RUN mkdir -p ${APP_HOME}/assets/data ${APP_HOME}/assets/models ${APP_HOME}/assets/cookies

# Set the working directory
WORKDIR ${APP_HOME}

CMD ["/start"]
