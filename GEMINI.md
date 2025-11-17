# GEMINI.md

## Project Overview

This project is a LinkedIn automation tool designed to automate various tasks on the LinkedIn platform. It utilizes Playwright for browser automation, with a stealth mode to minimize the risk of detection. The tool is containerized using Docker and includes a VNC server for visual debugging of the automation process.

The core functionalities of the tool, as described in the README, include:

*   **Automation Engine**: Using Playwright for browser automation.
*   **Campaigns**: Executing sequences of actions from YAML configuration.
*   **Scheduling**: Queuing actions for future execution.
*   **Local Database**: Storing scraped data and logs in SQLite.
*   **AI-powered Messaging**: Generating personalized messages using AI models.
*   **Analytics**: Tracking key performance metrics.


## Building and Running

The project is managed using Docker Compose and a `Makefile`.

*   **Build the Docker containers**:
    ```bash
    make build
    ```
*   **Start the application**:
    ```bash
    make up
    ```
*   **Stop the application**:
    ```bash
    make stop
    ```
*   **View logs**:
    ```bash
    make attach
    ```

The `docker-compose.yml` file (`local.yml`) defines the `app` service that runs the LinkedIn automation.

To view the browser automation, you can use a VNC viewer on your host machine to connect to `localhost:5900`.

## Development Conventions

### Dependencies

The main Python dependencies are `playwright` and `playwright-stealth`. These are installed directly in the `Dockerfile`. The `requirements/base.txt` file is currently empty.

### Configuration

The project's configuration is managed in `linkedin/conf.py`. Credentials like `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` are loaded from environment variables for security.

### Testing

The project uses `pytest` for testing, as indicated by the `pytest.ini` file. All tests are located in the `tests/` directory. We follow a Test-Driven Development (TDD) approach to ensure code quality and maintainability.

To run tests:
*   **Using Docker (recommended)**: `make test`
*   **Locally**: `pytest`
    *   If you encounter `PytestCacheWarning`s due to permissions, you can run tests with the cache disabled: `pytest -p no:cacheprovider`

### Code Style

The code seems to follow the PEP 8 style guide, but there is no linter configured to enforce it.

### Error Handling

The application should crash on unexpected errors. `try...except` blocks should be used sparingly and only for expected and recoverable errors. Do not use them to suppress exceptions broadly. We prefer explicit checks for potential errors, like using `os.path.exists()` before file access.

## Project Structure

<details>
<summary><code>docs</code></summary>

```
docs
├── architecture.md
└── testing_strategy.md
```

</details>

<details>
<summary><code>linkedin</code></summary>

```
linkedin
├── __init__.py
├── actions
│       ├── __init__.py
│        ├── login.py
│        └── search.py
├── campaign_parser.py
├── conf.py
├── templates
│        ├── notes
│        │       └── connect_note.j2
│        └── prompts
│            └── followup_prompt.j2
└── workflows.py
```

</details>