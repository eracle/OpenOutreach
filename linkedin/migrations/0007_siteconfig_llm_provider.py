from django.db import migrations, models


def infer_provider_from_existing_config(apps, schema_editor):
    """Set ``llm_provider`` on pre-existing rows based on ``llm_api_base``.

    Before this migration, the model only had ``llm_api_key`` / ``ai_model`` /
    ``llm_api_base``. A non-empty ``llm_api_base`` on an existing row means the
    user had configured an OpenAI-compatible endpoint (xAI, Groq via base URL,
    LM Studio, etc.); the field's plain ``default="openai"`` would silently
    re-route those rows to api.openai.com on first daemon start. Detect that
    case and set ``llm_provider="openai_compatible"``; leave fresh rows on
    the OpenAI default.
    """
    SiteConfig = apps.get_model("linkedin", "SiteConfig")
    SiteConfig.objects.filter(llm_api_base__gt="").update(llm_provider="openai_compatible")


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0006_update_default_limits"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="llm_provider",
            field=models.CharField(
                choices=[
                    ("openai", "OpenAI"),
                    ("anthropic", "Anthropic"),
                    ("google", "Google"),
                    ("groq", "Groq"),
                    ("mistral", "Mistral"),
                    ("cohere", "Cohere"),
                    ("openai_compatible", "OpenAI-compatible"),
                ],
                default="openai",
                max_length=32,
            ),
        ),
        migrations.RunPython(
            infer_provider_from_existing_config,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
