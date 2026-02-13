# Templating

The application uses a templating system to generate personalized follow-up messages. Two template types are
supported: `jinja` and `ai_prompt`.

The `render_template` function in `linkedin/templates/renderer.py` is responsible for processing templates.

**Important:** Profile fields are available as top-level variables — write `{{ first_name }}`, not
`{{ profile.first_name }}`. See the [Template Variables Reference](./template-variables.md) for the complete
list of available variables.

## Template Types

### 1. `jinja`

A `jinja` template uses the Jinja2 templating engine to insert profile data into messages dynamically.

**Example (`followup.j2`):**

```jinja2
Hey {{ full_name }},

I came across your profile and was impressed by your work{% if positions %} at {{ positions[0].company_name }}{% endif %}.
I'd love to stay in touch and explore potential synergies.

Best regards
```

### 2. `ai_prompt`

An `ai_prompt` template combines Jinja2 with a Large Language Model to generate human-like messages:

1. The template is first rendered as a Jinja2 template to create a prompt for the LLM.
2. This prompt is sent to the configured AI model (set via `AI_MODEL` in `accounts.secrets.yaml`).
3. The AI's response is used as the final message.

**Example (`followup_prompt.j2`):**

```jinja2
Write a short (2-3 sentences) follow-up message to {{ full_name }},
who is a {{ headline | default("professional") }}{% if positions %} at {{ positions[0].company_name }}{% endif %}.

{% if summary %}
Their profile summary:
{{ summary }}
{% endif %}

Be friendly and professional. Return ONLY the message — no quotes, no explanations.
```

To use this template type, you must have `LLM_API_KEY` set in your `accounts.secrets.yaml`.

## Configuration

Templates are configured per account in `accounts.secrets.yaml`:

```yaml
accounts:
  my_account:
    followup_template: templates/messages/followup.j2      # path relative to assets/
    followup_template_type: jinja                           # "jinja" or "ai_prompt"
    booking_link: https://calendly.com/your-link            # appended to every message (optional)
```

Template files live under `assets/templates/`:
- `assets/templates/messages/` — Jinja2 templates (type: `jinja`)
- `assets/templates/prompts/` — AI prompt templates (type: `ai_prompt`)

## Available Variables

See the [Template Variables Reference](./template-variables.md) for the complete list of profile fields,
nested structures (positions, educations, date ranges), and usage examples.
