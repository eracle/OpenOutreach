# Template Variables Reference

When writing follow-up message templates (Jinja2 or AI-prompt), the profile dictionary is spread directly into
the template context. This means all fields are available as top-level variables — you write `{{ first_name }}`,
not `{{ profile.first_name }}`.

The data comes from LinkedIn's Voyager API, parsed into a clean structure by `linkedin/api/voyager.py`.

## Top-Level Variables

| Variable | Type | Description | Example |
|:---------|:-----|:------------|:--------|
| `first_name` | string | First name | `"Jane"` |
| `last_name` | string | Last name | `"Doe"` |
| `full_name` | string | First + last name combined | `"Jane Doe"` |
| `headline` | string or null | Profile headline / tagline | `"VP of Engineering at Acme"` |
| `summary` | string or null | The "About" section text | `"15 years building..."` |
| `public_identifier` | string or null | LinkedIn handle (URL slug) | `"janedoe"` |
| `url` | string | Full LinkedIn profile URL | `"https://www.linkedin.com/in/janedoe/"` |
| `location_name` | string or null | Location as displayed on the profile | `"San Francisco, California"` |
| `geo` | dict or null | Structured geographic info (see below) | |
| `industry` | dict or null | Industry info (see below) | |
| `positions` | list of dicts | Work experience entries (see below) | |
| `educations` | list of dicts | Education entries (see below) | |
| `connection_degree` | int or null | Connection degree (1 = connected, 2 = 2nd, 3 = 3rd) | `2` |
| `connection_distance` | string or null | Raw distance value from the API | `"DISTANCE_2"` |
| `urn` | string | LinkedIn internal URN identifier | |

## Positions

Each entry in the `positions` list is a dict with these fields:

| Field | Type | Description | Example |
|:------|:-----|:------------|:--------|
| `title` | string | Job title | `"Senior Engineer"` |
| `company_name` | string | Company name | `"Acme Corp"` |
| `company_urn` | string or null | LinkedIn URN for the company | |
| `location` | string or null | Position-specific location | `"New York, NY"` |
| `description` | string or null | Role description text | `"Led a team of 12..."` |
| `date_range` | dict or null | Start/end dates (see Date Range below) | |
| `urn` | string or null | LinkedIn internal URN for this position | |

### Accessing positions in templates

```jinja2
{# Current company (first position) #}
{{ positions[0].company_name if positions else "their company" }}

{# Current title #}
{{ positions[0].title if positions else "professional" }}

{# Loop over all positions #}
{% for pos in positions %}
- {{ pos.title }} at {{ pos.company_name }}
{% endfor %}
```

## Educations

Each entry in the `educations` list is a dict with these fields:

| Field | Type | Description | Example |
|:------|:-----|:------------|:--------|
| `school_name` | string | School or university name | `"MIT"` |
| `degree_name` | string or null | Degree type | `"Bachelor of Science"` |
| `field_of_study` | string or null | Field/major | `"Computer Science"` |
| `date_range` | dict or null | Start/end dates (see Date Range below) | |
| `urn` | string or null | LinkedIn internal URN | |

### Accessing educations in templates

```jinja2
{# First school #}
{{ educations[0].school_name if educations else "" }}

{# Degree and field #}
{{ educations[0].degree_name ~ " in " ~ educations[0].field_of_study if educations and educations[0].degree_name else "" }}
```

## Date Range

Position and education entries may have a `date_range` dict with this structure:

```json
{
  "start": {"year": 2020, "month": 3},
  "end": {"year": 2024, "month": 12}
}
```

- `start` and `end` are dicts with `year` (int or null) and `month` (int or null).
- A null `end` means the position is current.

```jinja2
{# Check if currently employed at first position #}
{% if positions and positions[0].date_range and positions[0].date_range.end is none %}
  Currently at {{ positions[0].company_name }}
{% endif %}
```

## Geo and Industry

The `geo` and `industry` fields are dicts with API-specific keys:

```jinja2
{# Industry name #}
{{ industry.name if industry else "" }}

{# Geo region (localized name without country) #}
{{ geo.defaultLocalizedNameWithoutCountryName if geo else "" }}
```

## Null Safety

Many fields can be null. Use Jinja2's `default` filter or conditional checks to handle missing data
gracefully:

```jinja2
{# Using the default filter #}
{{ headline | default("a professional") }}
{{ location_name | default("") }}

{# Conditional blocks #}
{% if summary %}
About: {{ summary }}
{% endif %}

{# Safe access to nested data #}
{{ positions[0].company_name if positions else "their company" }}
```

## Complete Example (Jinja2 template)

```jinja2
Hey {{ first_name }},

I saw you're working as {{ headline | default("a professional") }}{% if positions %} at {{ positions[0].company_name }}{% endif %}{% if location_name %} in {{ location_name }}{% endif %}.

{% if summary %}I was particularly interested in your background — {{ summary[:100] }}...{% endif %}

Would love to connect and exchange ideas.

Best regards
```

## Complete Example (AI-prompt template)

```jinja2
Write a short (2-3 sentences) follow-up message to {{ full_name }},
who is a {{ headline | default("professional") }}{% if positions %} at {{ positions[0].company_name }}{% endif %}.

{% if summary %}
Their profile summary:
{{ summary }}
{% endif %}

{% if positions|length > 1 %}
Previous roles: {% for pos in positions[1:3] %}{{ pos.title }} at {{ pos.company_name }}{{ ", " if not loop.last }}{% endfor %}
{% endif %}

Be friendly and professional. End with a soft call-to-action.
Return ONLY the message — no quotes, no explanations.
```
