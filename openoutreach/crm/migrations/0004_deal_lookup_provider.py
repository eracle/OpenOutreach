from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0003_rename_no_email_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="lookup_provider",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
