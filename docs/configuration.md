# Configuration

The application's configuration is managed through a combination of a central YAML file for account settings and
environment variables for sensitive keys.

## Account Configuration (`accounts.secrets.yaml`)

All account-related settings are defined in `assets/accounts.secrets.yaml`. You can have multiple accounts defined in
this file, each with its own settings.

To get started, copy the template file:

```bash
cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml
```

### Account Structure

Each account is defined by a unique `handle` (e.g., `jane_doe_main`). Here are the available fields for each account:

| Field               | Type    | Description                                                                      | Default |
|:--------------------|:--------|:---------------------------------------------------------------------------------|:--------|
| `active`            | boolean | If `true`, this account will be included in campaign runs.                       | `true`  |
| `proxy`             | string  | The URL of a proxy to use for this account (e.g., `http://user:pass@host:port`). | `null`  |
| `daily_connections` | integer | The maximum number of connection requests to send per day.                       | `50`    |
| `daily_messages`    | integer | The maximum number of messages to send per day.                                  | `20`    |
| `username`          | string  | The LinkedIn email address for the account.                                      | (none)  |
| `password`          | string  | The LinkedIn password for the account.                                           | (none)  |

### Derived Paths

The system automatically generates the following paths for each account based on its handle:

- **Cookie File**: `assets/cookies/<handle>.json`
- **Database File**: `assets/data/<handle>.db`

## Environment Variables

Sensitive information and global settings are configured using environment variables. These can be set in a `.env` file
in the project root.

| Variable         | Description                                                                          | Default       |
|:-----------------|:-------------------------------------------------------------------------------------|:--------------|
| `LLM_API_KEY`  | Your API key for any OpenAI-compatible provider (e.g., OpenAI, xAI, Anthropic).      | (none)        |
| `LLM_API_BASE` | Base URL for the API (e.g., `https://api.x.ai/v1/`, `https://api.openai.com/v1`).   | (none)        |
| `AI_MODEL`       | The model to use for message generation.                                             | `gpt-4o-mini` |
