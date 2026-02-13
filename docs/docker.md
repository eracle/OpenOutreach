# Docker Installation and Usage

This guide provides instructions for running the application using Docker, which is the recommended method for a stable and consistent environment.

### Prerequisites

- [Make](https://www.gnu.org/software/make/)
- [Docker](https://www.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)
- [Git](https://git-scm.com/)

## ⚡ Quick Start

Get up and running in minutes.

```bash
# 1. Clone the repository
git clone https://github.com/eracle/OpenOutreach.git
cd OpenOutreach

# 2. Build and start the application
make up
```

This command will build the Docker image and start the automation service.

### Configuration

Before running, you need to set up your credentials and target profiles:

1.  **Configure LinkedIn accounts**
    ```bash
    cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml
    ```
    Then, edit `assets/accounts.secrets.yaml` with your LinkedIn username and password. You can add multiple accounts, but only one should be marked as `active: true`.

Once configured, running `make up` will launch the default campaign. The process is fully resumable, so you can stop (`make stop`) and restart it at any time without losing progress.

## 🖥️ Visual Debugging with VNC

The Docker container includes a VNC server, allowing you to watch the automation live. This is useful for debugging and seeing exactly what the tool is doing.

**Option 1: Using the Makefile command (for Linux with `vinagre`)**

If you are on Linux and have `vinagre` installed, you can use the built-in Make command:
```bash
make up-view
```
This will start the services in the background and automatically open a VNC viewer connected to the session.

**Option 2: Manual Connection**

On any operating system, you can connect manually:

1.  Start the application normally: `make up`
2.  Open your favorite VNC client (e.g., [RealVNC Viewer](https://www.realvnc.com/en/connect/download/viewer/), [TightVNC](https://www.tightvnc.com/)).
3.  Connect to the address: `localhost:5900`.
4.  No password is required by default.

This will open a window showing the virtual desktop inside the container where the browser is running.

## useful-commands

- `make stop`: Stops the running containers.
- `make attach`: Follows the application logs.
- `make test`: Runs the test suite.
