# Template Variables Reference

This document describes the variables available in the follow-up agent's system prompt template
(`linkedin/templates/prompts/follow_up_agent.j2`) and the data structures from LinkedIn's Voyager API.

## Agent System Prompt Variables

The follow-up agent template receives these named variables (not a spread profile dict):

| Variable | Type | Description | Example |
|:---------|:-----|:------------|:--------|
| `self_name` | string | The logged-in user's name (from `/in/me/` sentinel) | `"Jane Doe"` |
| `product_docs` | string | Product/service description from Campaign | |
| `campaign_objective` | string | Campaign goal from Campaign | |
| `booking_link` | string | Calendar link from Campaign | `"https://calendly.com/your-link"` |
| `full_name` | string | Lead's first + last name | `"John Smith"` |
| `headline` | string or null | Lead's profile headline | `"VP of Engineering at Acme"` |
| `current_company` | string or null | Company from first position | `"Acme Corp"` |
| `location` | string or null | Location as displayed on profile | `"San Francisco, California"` |
| `past_messages_count` | integer | Number of previous outgoing messages to this lead | `2` |

## Voyager API Profile Structure

The profile data parsed by `linkedin/api/voyager.py` contains the following fields. These are stored
in `Lead.description` as JSON and used internally for enrichment and qualification, though only a subset
is passed to the agent template.

### Top-Level Fields

| Field | Type | Description |
|:------|:-----|:------------|
| `first_name` | string | First name |
| `last_name` | string | Last name |
| `full_name` | string | First + last name combined |
| `headline` | string or null | Profile headline / tagline |
| `summary` | string or null | The "About" section text |
| `public_identifier` | string or null | LinkedIn handle (URL slug) |
| `url` | string | Full LinkedIn profile URL |
| `location_name` | string or null | Location as displayed on profile |
| `geo` | dict or null | Structured geographic info |
| `industry` | dict or null | Industry info |
| `positions` | list of dicts | Work experience entries |
| `educations` | list of dicts | Education entries |
| `connection_degree` | int or null | Connection degree (1 = connected, 2 = 2nd, 3 = 3rd) |
| `connection_distance` | string or null | Raw distance value from the API |
| `urn` | string | LinkedIn internal URN identifier |

### Positions

Each entry in the `positions` list:

| Field | Type | Description |
|:------|:-----|:------------|
| `title` | string | Job title |
| `company_name` | string | Company name |
| `company_urn` | string or null | LinkedIn URN for the company |
| `location` | string or null | Position-specific location |
| `description` | string or null | Role description text |
| `date_range` | dict or null | Start/end dates (see below) |
| `urn` | string or null | LinkedIn internal URN for this position |

### Educations

Each entry in the `educations` list:

| Field | Type | Description |
|:------|:-----|:------------|
| `school_name` | string | School or university name |
| `degree_name` | string or null | Degree type |
| `field_of_study` | string or null | Field/major |
| `date_range` | dict or null | Start/end dates (see below) |
| `urn` | string or null | LinkedIn internal URN |

### Date Range

Position and education entries may have a `date_range` dict:

```json
{
  "start": {"year": 2020, "month": 3},
  "end": {"year": 2024, "month": 12}
}
```

- `start` and `end` are dicts with `year` (int or null) and `month` (int or null).
- A null `end` means the position is current.

### Geo and Industry

The `geo` and `industry` fields are dicts with API-specific keys:

```
geo.defaultLocalizedNameWithoutCountryName  →  "San Francisco Bay Area"
industry.name                                →  "Computer Software"
```

## Null Safety

Many fields can be null. In Jinja2 templates, use the `default` filter or conditional checks:

```jinja2
{{ headline | default("a professional") }}
{% if positions %}Current company: {{ positions[0].company_name }}{% endif %}
```
