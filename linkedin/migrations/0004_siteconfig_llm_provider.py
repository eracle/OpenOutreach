from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0003_siteconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="llm_provider",
            field=models.CharField(
                choices=[("openai", "OpenAI"), ("gemini", "Google Gemini")],
                default="gemini",
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="ai_model",
            field=models.CharField(blank=True, default="gemini-2.5-flash-lite", max_length=200),
        ),
    ]
