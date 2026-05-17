from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0007_siteconfig_llm_provider"),
    ]

    operations = [
        migrations.AlterField(
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
                    ("litellm", "LiteLLM"),
                ],
                default="openai",
                max_length=32,
            ),
        ),
    ]
