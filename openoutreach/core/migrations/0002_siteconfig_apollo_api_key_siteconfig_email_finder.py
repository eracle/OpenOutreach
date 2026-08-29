from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="apollo_api_key",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="email_finder",
            field=models.CharField(
                blank=True, default="", max_length=32,
                help_text="bettercontact | apollo — blank picks whichever key is configured",
            ),
        ),
    ]
