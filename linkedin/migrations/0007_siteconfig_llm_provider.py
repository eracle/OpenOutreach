from django.db import migrations, models


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
    ]
