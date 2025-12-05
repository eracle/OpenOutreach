# System Architecture

This document outlines the architecture of the LinkedIn automation tool, from data ingestion and storage to the workflow
execution engine.

## High-Level Overview

The system is designed to automate interactions on LinkedIn based on configurable campaigns. The core workflow is as
follows:

1. **Input**: LinkedIn profile or company URLs are provided via CSV files.
2. **Data Caching**: The system scrapes detailed information for each URL and stores it in a local SQLite database per
   account(`data/<handle>.db`). This database acts as a cache to avoid redundant scraping.
3. **Campaign Execution**: Campaigns are defined as Python modules that orchestrate a sequence of actions (e.g., send
   connection request, send message) for each entity.
4. **State Management**: The state of the campaign (e.g., which profiles have been processed) is tracked in the same
   per-account SQLite database.

## Core Entities

The system revolves around a single primary data model: `Profile`. This entity is represented as a JSON
object throughout the application, from the database to the business logic.

## Database

The application uses a separate SQLite database for each account, located at `assets/data/<handle>.db`. This database
handles all persistence needs, including data caching and campaign state tracking.

### Database Schema

The database contains two main tables: `profiles` and `campaign_runs`.

**`profiles` table**
| Column | Type | Description |
| :--- | :--- | :--- |
| `url` | TEXT | PRIMARY KEY. The full LinkedIn profile URL. |
| `data` | JSON | A JSON object containing the parsed, structured profile data. |
| `raw_json` | JSON | The complete, unmodified JSON response from the Voyager API. |
| `cloud_synced` | BOOLEAN | A flag indicating if the profile has been synced to an external CRM. |
| `created_at` | DATETIME | Timestamp of when the record was created. |
| `updated_at` | DATETIME | Timestamp of when the record was last updated. |

**`campaign_runs` table**
| Column | Type | Description |
| :--- | :--- | :--- |
| `name` | TEXT | PRIMARY KEY. The user-defined name of the campaign. |
| `handle` | TEXT | PRIMARY KEY. The handle of the account running the campaign. |
| `input_hash` | TEXT | PRIMARY KEY. A hash of the input (e.g., CSV file) to uniquely identify the run. |
| `run_at` | DATETIME | Timestamp of when the campaign was started. |
| `short_id` | TEXT | A short, unique, human-readable ID for the campaign run. |
| `total_profiles` | INTEGER | The total number of profiles in the campaign. |
| `enriched` | INTEGER | A counter for the number of profiles successfully enriched. |
| `connect_sent` | INTEGER | A counter for the number of connection requests sent. |
| `accepted` | INTEGER | A counter for the number of connections accepted. |
| `followup_sent` | INTEGER | A counter for the number of follow-up messages sent. |
| `completed` | INTEGER | A counter for the number of profiles that completed the campaign. |
| `last_updated` | DATETIME | Timestamp of when the campaign statistics were last updated. |


## API Client

The `linkedin/api` module is responsible for all direct communication with LinkedIn's internal "Voyager" API. This
provides a more reliable and structured way to fetch data compared to scraping HTML.

- **`client.py`**: Defines the `PlaywrightLinkedinAPI` class, which uses the browser's active `Playwright` context to
  make authenticated GET requests. It automatically extracts the necessary `csrf-token` and headers to mimic a
  legitimate browser request, making it resilient to basic anti-bot measures.

- **`voyager.py`**: Contains the data parsing logic. The Voyager API returns a complex JSON response with entities and
  references. This module traverses the JSON graph, resolves the references, and maps the raw data to clean, structured
  `LinkedInProfile` dataclasses. This isolates the messy data parsing from the rest of the application.

- **`logging.py`**: A simple utility for logging API responses, primarily for debugging purposes.

## Navigation

The `linkedin/navigation` module handles all browser automation tasks using Playwright. Its primary goal is to reliably
manage the browser state and simulate human-like interactions to avoid detection.

- **`login.py`**: Automates the entire login process. It navigates to the login page, enters credentials, and handles
  multi-factor authentication if prompted. Crucially, it manages session state by saving and loading cookies, allowing
  the bot to stay logged in across multiple runs.

- **`utils.py`**: Provides a set of helper functions for robust browser control. This includes `human_delay` for
  realistic pauses and `navigate_and_verify` for safely performing actions (like clicks or URL visits) and confirming
  the outcome.

- **`errors.py`**: Defines custom exceptions for common automation failures, such as `ProfileNotFoundInSearchError` or
  `AuthenticationError`.

- **`enums.py`**: Contains simple enumerations (`ConnectionStatus`, `MessageStatus`) used to standardize state tracking
  throughout the application.

## Campaigns

Campaigns are high-level workflows defined in the `linkedin/campaigns` directory. Unlike the previous YAML-based
approach, campaigns are now implemented as Python modules (e.g., `connect_follow_up.py`).

Each module orchestrates a sequence of calls to the `linkedin/actions` module to execute a specific outreach strategy.
For example, the `connect_follow_up` campaign:

1. Enriches a profile with the latest data.
2. Sends a connection request.
3. If already connected, immediately sends a follow-up message.

This Python-native approach provides greater flexibility and makes the logic easier to debug and maintain. It creates a
clean separation between the high-level workflow (the campaign) and the low-level, reusable browser tasks (the actions).

## Actions

Actions are the core building blocks of any campaign, located in the `linkedin/actions/` directory. They are modular,
reusable functions that perform a single, specific task within the browser. Campaigns orchestrate these actions to
create complex automation workflows.

### `connect.py`

- **`send_connection_request`**: This is the primary function for connecting with a profile. It first navigates to the
  profile page and checks the current connection status. If not already connected or pending, it sends a connection
  request *without* a note for maximum efficiency and acceptance rate. The logic for sending a note is preserved but
  currently disabled.

### `connection_status.py`

- **`get_connection_status`**: Determines the relationship with a profile. It first attempts to use the
  `connection_degree` from the structured Voyager API data, which is fast and reliable. If the API data is unavailable,
  it falls back to inspecting the user interface for visual cues like a "Pending" button or a "1st" degree connection
  badge.

### `message.py`

- **`send_follow_up_message`**: Orchestrates sending a message to a profile. It renders the message from a template and
  calls the sending logic.
- **`send_message_to_profile`**: Before sending, it calls `get_messaging_availability` to ensure a message can be sent (
  i.e., the profiles are connected). If available, it executes the low-level `_perform_send_message` function, which
  includes a robust fallback to using the clipboard if standard typing fails.

### `profile.py`

- **`enrich_profile`**: This action uses the `PlaywrightLinkedinAPI` client to fetch detailed, structured data from
  LinkedIn's internal Voyager API. It parses the response and returns a clean, enriched profile dictionary, which is
  then used for personalized messaging or other actions.

