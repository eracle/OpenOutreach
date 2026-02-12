# Templating

The application uses a powerful templating system to generate personalized messages and connection notes. This system
supports three types of templates: `jinja`, and `ai_prompt`.

The `render_template` function in `linkedin/templates/renderer.py` is responsible for processing these templates.

## Template Types

### 1. `jinja`

A `jinja` template allows you to use the powerful Jinja2 templating engine to insert profile data into your messages
dynamically. This is useful for personalizing messages with the person's name, company, or other details.

The template has access to the `profile` object, which contains all the data scraped from the person's LinkedIn profile.

**Example (`connect_note.j2`):**

```jinja2
Hi {{ profile.first_name }},

I saw that you're working at {{ profile.positions[0].company_name }}. I'm also in the industry and would love to connect.

Best,
[Your Name]
```

### 2. `ai_prompt`

An `ai_prompt` template combines the power of Jinja2 with a Large Language Model (LLM) like GPT-4 to generate highly
personalized and human-like messages.

The process is as follows:

1. The template is first rendered as a Jinja2 template to create a prompt for the LLM.
2. This prompt is then sent to the configured AI model (e.g., `gpt-4o-mini`).
3. The AI's response is used as the final message.

This allows you to create dynamic and context-aware messages based on the person's profile.

**Example (`followup_prompt.j2`):**

```jinja2
You are a friendly and professional salesperson. Write a short (2-3 sentences) follow-up message to {{ profile.full_name }}, who is a {{ profile.headline }} at {{ profile.positions[0].company_name }}.

Here are some key points from their profile summary:
{{ profile.summary }}

Mention something about their recent work and ask if they are open to a quick chat.
```

To use this template type, you must have your `LLM_API_KEY` environment variable set.
